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
- non-date operators and epoch values pass through untouched
- an unparseable or invalid date raises ``ValueError`` loudly BEFORE any
  backend call — never a silent zero-result
- ``newer_than:``/``older_than:`` duration values are validated too (#2830):
  the unsupported ``w`` (weeks) unit — silently zeroed by Gmail with no
  error — is converted to days; ``h``/``d``/``m``/``y`` pass through
  byte-identical; anything else raises loudly

All tests are hermetic: FakeGmailBackend only, no Lemonade, no network.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.read_tools import (  # noqa: E402
    ReadToolsMixin,
    normalize_gmail_date_operators,
    search_messages_impl,
)

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402
from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402

# ---------------------------------------------------------------------------
# End-to-end through search_messages_impl: outgoing query is normalized
# ---------------------------------------------------------------------------


def test_impl_normalizes_mixed_format_dates_in_outgoing_query():
    gmail = FakeGmailBackend(user_email="user@example.com")
    search_messages_impl(gmail, query="invoice after:July 1, 2026 before:2026-07-08")
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
        # newer_than:/older_than: ARE now parsed (#2830) -- these two stay
        # unchanged because they're already-valid duration values, not
        # because the operator is skipped.
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
    assert normalize_gmail_date_operators("after:July 1") == f"after:{year}/07/01"


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


# ---------------------------------------------------------------------------
# Duration-operator validation: newer_than: / older_than: (#2830)
#
# Gmail silently returns zero results for a duration value it doesn't
# understand -- no error, indistinguishable from an empty mailbox. `w`
# (weeks) is the one unit a model reaches for that Gmail does not implement;
# converting it to days is the actual fix for the reported "0 messages" bug.
# Accept-list measured directly against live Gmail, not read from a doc
# (Gmail's own docs omit `h` entirely).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ('from:"The Neuron" newer_than:2w', 'from:"The Neuron" newer_than:14d'),
        ("older_than:3w", "older_than:21d"),
        ("newer_than:1w", "newer_than:7d"),
        # Quoted value: still converted, not silently bypassed by the quotes.
        ('newer_than:"2w"', "newer_than:14d"),
    ],
)
def test_converts_unsupported_week_unit_to_days(query, expected):
    assert normalize_gmail_date_operators(query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "newer_than:12h",  # h (hours) -- Gmail accepts it; not in its own docs
        "newer_than:14D",  # case is left as-is -- not renormalized to lowercase
        "newer_than:336h",
        "newer_than:1m",
        "newer_than:1y",
        "older_than:1y",
    ],
)
def test_duration_values_already_valid_pass_through_byte_identical(query):
    assert normalize_gmail_date_operators(query) == query


def test_impl_finds_message_via_converted_week_unit():
    """End-to-end proof the conversion has real search effect, not just a
    string rewrite: the fake backend does NOT accept 'w' natively (#2830),
    so this only passes because normalize_gmail_date_operators converts the
    query to 'd' before the backend ever sees it."""
    gmail = FakeGmailBackend(user_email="user@example.com")
    now_ms = int(time.time() * 1000)
    gmail.add_message(
        {
            "id": "m1",
            "threadId": "m1",
            "labelIds": ["INBOX"],
            "snippet": "hi",
            "internalDate": str(now_ms),
            "payload": {
                "mimeType": "text/plain",
                "filename": "",
                "headers": [
                    {"name": "From", "value": "news@x.com"},
                    {"name": "Subject", "value": "Fresh"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
                ],
                "body": {"data": "", "size": 0},
            },
            "sizeEstimate": 0,
        }
    )
    result = search_messages_impl(gmail, query="from:news@x.com newer_than:2w")
    assert [m["id"] for m in result["messages"]] == ["m1"]


def test_fixture_query_matches_rejects_week_unit_explicitly():
    """Pins FakeGmailBackend's own behavior for the unsupported 'w' unit,
    not just that normalize_gmail_date_operators converts it away --
    _query_matches falls through an unrecognized duration to its free-text
    branch (a fall-through, not an explicit exclusion rule), so a future
    refactor of that fallback could silently re-accept 'w' without any
    other test noticing. This is exactly how #2830 survived."""
    from tests.fixtures.email.fake_gmail import _query_matches

    now_ms = int(time.time() * 1000)
    msg = {
        "id": "m1",
        "internalDate": str(now_ms),
        "labelIds": ["INBOX"],
        "snippet": "hi",
        "payload": {"headers": [{"name": "Subject", "value": "hi"}]},
    }
    assert _query_matches("newer_than:2w", msg) is False


def test_duration_space_after_colon_is_a_known_unvalidated_gap():
    """Known, accepted limitation: a space between the operator colon and
    the value means _DURATION_OP_RE doesn't match at all (mirrors
    _DATE_OP_RE's identical-shaped gap for after:/before:), so the value
    passes through to Gmail completely unvalidated. Deliberately not widened
    with \\s* -- that would diverge from the date-operator precedent and
    turn a never-observed query shape into a hard error."""
    query = "newer_than: 2w"
    assert normalize_gmail_date_operators(query) == query


@pytest.mark.parametrize(
    "query",
    [
        "newer_than:14",  # missing unit
        "newer_than:1.5d",  # non-integer count
        "newer_than:abc",  # not a number at all
        "newer_than:1.5w",  # non-integer count -- even for the convertible unit
        "newer_than:-3d",  # negative count
        "newer_than:2weeks",  # multi-letter unit -- only bare 'w' converts
        "older_than:2x",  # unrecognized single-letter unit
    ],
)
def test_unparseable_duration_raises_actionable_error(query):
    with pytest.raises(ValueError, match=r"h/d/m/y"):
        normalize_gmail_date_operators(query)


def test_duration_12h_does_not_raise():
    """`h` must NOT be rejected -- it's a working Gmail query (measured: 1
    result), even though Gmail's own docs omit it."""
    assert normalize_gmail_date_operators("newer_than:12h") == "newer_than:12h"


def test_duration_error_names_offending_value():
    with pytest.raises(ValueError) as exc:
        normalize_gmail_date_operators("newer_than:1.5d")
    assert repr("1.5d") in str(exc.value)


def test_impl_rejects_unparseable_duration_before_any_backend_call():
    gmail = FakeGmailBackend(user_email="user@example.com")
    with pytest.raises(ValueError, match=r"h/d/m/y"):
        search_messages_impl(gmail, query="newer_than:1.5d")
    assert gmail.transport.calls == []


# ---------------------------------------------------------------------------
# AC 3d: the ValueError reaches the model as a structured tool error, not a
# traceback -- the registered @tool wrapper's broad "except Exception" was
# already there for ConnectorsError; this pins that it also catches the new
# duration ValueError rather than letting it kill the turn.
# ---------------------------------------------------------------------------


class _Host(ReadToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface (mirrors
    test_search_messages_metadata_only_2763.py's fixture)."""

    def __init__(self, backend: FakeGmailBackend):
        self._gmail = backend
        self._backends = {"google": backend}
        self._message_mailbox: dict = {}
        self.config = SimpleNamespace(debug=False)

    def _remember_message_mailbox(self, message_id, provider):
        if message_id:
            self._message_mailbox[message_id] = provider


def test_registered_search_messages_returns_structured_error_for_bad_duration():
    gmail = FakeGmailBackend(user_email="user@example.com")
    host = _Host(gmail)
    _TOOL_REGISTRY.clear()
    host._register_read_tools()
    search_messages = _TOOL_REGISTRY["search_messages"]["function"]

    payload = json.loads(search_messages(query="newer_than:1.5d", max_results=25))

    assert payload["ok"] is False
    assert "1.5d" in payload["error"]


# ---------------------------------------------------------------------------
# Effective-query logging: the log MESSAGE (not just tool_call's structured
# ``extra``) must carry the post-normalization query and retry state (#2830
# increment 3) -- greppable proof, in ~/.gaia/gaia.log, of what query Gmail
# actually saw when a report of "0 messages" turns out to be a bad unit.
# ---------------------------------------------------------------------------


def test_search_messages_logs_effective_query_when_retry_not_needed(caplog):
    gmail = FakeGmailBackend(user_email="user@example.com")
    with caplog.at_level("INFO", logger="gaia_agent_email"):
        search_messages_impl(gmail, query='from:"alice@example.com" newer_than:2w')
    query_records = [
        r for r in caplog.records if r.getMessage().startswith("search_messages ")
    ]
    assert len(query_records) == 1
    message = query_records[0].getMessage()
    # Post-normalization: the unsupported 'w' unit converted to 'd'.
    assert "newer_than:14d" in message
    # Address-bearing query renders redacted, not the raw address (#2830, 68a16a77).
    assert "alice@example.com" not in message
    assert "[REDACTED]" in message
    # An operator query never triggers the widen retry -- state says so.
    assert "retry=none" in message


def test_search_messages_logs_retry_state_when_widen_fires(caplog):
    gmail = FakeGmailBackend(user_email="user@example.com")
    with caplog.at_level("INFO", logger="gaia_agent_email"):
        search_messages_impl(gmail, query="Last Week in AI")
    query_records = [
        r for r in caplog.records if r.getMessage().startswith("search_messages ")
    ]
    assert len(query_records) == 1
    message = query_records[0].getMessage()
    assert "retried_to=" in message
    assert "from:(Last Week in AI) OR subject:(Last Week in AI)" in message
