# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Date-operator normalization tests for ``search_messages`` (#2161).

Gmail requires ``after:``/``before:``/``older:``/``newer:`` values in
``YYYY/MM/DD`` form. The model routinely emits natural-language dates
(``after:July 1 before:July 8``), which Gmail treats as free-text content
matches — silently returning 0 results and letting the agent confidently
assert no messages exist in the range.

Covered:

- mixed-format query normalizes to ``after:YYYY/MM/DD before:YYYY/MM/DD``
  in the outgoing Gmail query string (asserted via FakeGmailBackend's
  recorded transport call)
- the common formats the model produces parse: ``July 1``, ``July 1 2026``,
  ``July 1, 2026``, ``1 July 2026``, ``2026-07-01``, ``7/1/2026``,
  ``2026/7/1`` (zero-padded)
- non-date operators (``newer_than:7d``) and epoch values pass through
  untouched
- an unparseable or invalid date raises ``ValueError`` loudly BEFORE any
  backend call — never a silent zero-result

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    normalize_gmail_date_operators,
    search_messages_impl,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


# ---------------------------------------------------------------------------
# End-to-end through search_messages_impl: outgoing query is normalized
# ---------------------------------------------------------------------------


def test_impl_normalizes_mixed_format_dates_in_outgoing_query():
    gmail = FakeGmailBackend(user_email="user@example.com")
    search_messages_impl(
        gmail, query="invoice after:July 1, 2026 before:2026-07-08"
    )
    listed = [c for c in gmail.transport.calls if c[0] == "list_messages"]
    assert len(listed) == 1
    assert listed[0][1]["query"] == "invoice after:2026/07/01 before:2026/07/08"


def test_impl_rejects_unparseable_date_before_any_backend_call():
    gmail = FakeGmailBackend(user_email="user@example.com")
    with pytest.raises(ValueError, match=r"YYYY/MM/DD"):
        search_messages_impl(gmail, query="after:sometime")
    assert gmail.transport.calls == []


# ---------------------------------------------------------------------------
# Normalizer unit coverage: formats the model actually produces
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("after:2026-07-01", "after:2026/07/01"),
        ("after:7/1/2026", "after:2026/07/01"),
        ("after:July 1 2026", "after:2026/07/01"),
        ("after:July 1, 2026", "after:2026/07/01"),
        ("before:1 July 2026", "before:2026/07/01"),
        ('before:"July 8, 2026"', "before:2026/07/08"),
        ("after:jul 1st 2026", "after:2026/07/01"),
        ("older:2026-12-31", "older:2026/12/31"),
        ("newer:2026-01-02", "newer:2026/01/02"),
        # Already-Gmail values normalize idempotently (zero-padded).
        ("after:2026/7/1", "after:2026/07/01"),
        ("after:2026/07/01", "after:2026/07/01"),
        # Multiple operators plus surrounding free text.
        (
            "from:boss@example.com after:July 1 2026 before:July 8 2026 is:unread",
            "from:boss@example.com after:2026/07/01 before:2026/07/08 is:unread",
        ),
    ],
)
def test_normalizes_common_model_formats(query, expected):
    assert normalize_gmail_date_operators(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "newer_than:7d",
        "older_than:2m",
        "from:boss@example.com is:unread",
        "after:1751328000",  # epoch seconds are valid Gmail date values
        "",
    ],
)
def test_non_date_operators_and_epoch_pass_through_untouched(query):
    assert normalize_gmail_date_operators(query) == query


def test_yearless_date_defaults_to_current_year():
    year = date.today().year
    assert (
        normalize_gmail_date_operators("after:July 1")
        == f"after:{year}/07/01"
    )


# ---------------------------------------------------------------------------
# Loud failure: unparseable / impossible dates never pass through
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "after:sometime",
        "before:next week",
        "after:2026-02-30",  # impossible calendar date
        "after:13/45/2026",
    ],
)
def test_unparseable_date_raises_actionable_error(query):
    with pytest.raises(ValueError, match=r"YYYY/MM/DD"):
        normalize_gmail_date_operators(query)
