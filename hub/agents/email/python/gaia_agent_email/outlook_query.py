# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Gmail-operator -> Microsoft Graph query translation for ``LiveOutlookBackend`` (#2996).

``list_messages`` used to wrap ANY non-empty query string in quotes and hand
it to Graph's ``$search`` as one exact phrase, so a Gmail-style operator
sitting inside that string (``from:``, ``is:unread``, ``newer_than:7d``, ...)
reached Graph as literal characters instead of a scope, and Graph never
parsed it: the search returned nothing for a user with only an Outlook
mailbox connected.

This module splits that string into what each half of it actually is:

- ``is:unread``/``is:read`` and ``newer_than:``/``older_than:`` describe
  filters Graph's mail ``$search`` (a KQL subset over subject/body/
  participants) does not expose; only ``$filter`` can express "unread" or a
  date bound, so these are extracted and rendered as an OData ``$filter``.
- Everything else, including a bare phrase or ``from:``/``subject:``, stays
  in the ``$search`` string. Graph's docs (Use the search query parameter,
  learn.microsoft.com/en-us/graph/search-query-parameter) show the whole KQL
  clause wrapped in one pair of double quotes, e.g. ``$search="from:randiw"``:
  the wrapping does not demote ``from:`` to a literal, Graph's KQL parser
  reads it inside the quotes. So every ``$search`` value this module returns
  is quoted and escaped the same way #3021 established (quotes/backslashes
  already in the query, e.g. ``from:"Acme Corp"``, are escaped so they don't
  collide with the wrapping quotes).
- Graph's mail endpoint rejects ``$search`` and ``$filter`` in the same
  request, so a query mixing the two families (``is:unread from:alice``)
  cannot be expressed in one call; that raises rather than silently dropping
  one half.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from gaia_agent_email.gmail_query import parse_gmail_duration_value

# The only two Gmail ``is:`` values with an unambiguous Graph $filter mapping
# (``isRead``); any other value (``starred``, ...) is left as free text, which
# the mixed-family check below turns into a loud error, not a silent no-op.
_IS_RE = re.compile(r"\bis:(unread|read)\b", re.IGNORECASE)

_DURATION_RE = re.compile(
    # Stop at grouping punctuation so `(newer_than:7d)` validates `7d`, not
    # `7d)`, just like the Gmail query normalizer.
    r'\b(?P<op>newer_than|older_than):(?P<val>"[^"]*"|[^\s)}\]]+)',
    re.IGNORECASE,
)

# Graph has no calendar-aware relative-date filter, so a month is 30 days
# and a year is 365, the same approximation Gmail's own "1 month ago"
# reading makes, and precise enough for a recency window, not a ledger.
_DURATION_UNIT_DAYS = {"d": 1, "m": 30, "y": 365}


def _graph_search_param(query: str) -> str:
    """Wrap KQL for Graph ``$search``.

    Graph requires the whole KQL string to be wrapped in double quotes.
    Quotes and backslashes inside the query must be escaped, otherwise a
    value such as ``from:"Acme Corp"`` becomes ``"from:"Acme Corp""``.
    """
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _duration_to_cutoff(op: str, raw_value: str, *, now: datetime) -> str:
    # Reuses #2830's validator so an out-of-range/misspelled unit raises the
    # same actionable ValueError here as it would for the Gmail path.
    normalized = parse_gmail_duration_value(raw_value, op=op)
    m = re.fullmatch(r"(\d+)([A-Za-z])", normalized)
    amount, unit = int(m.group(1)), m.group(2).lower()
    delta = (
        timedelta(hours=amount)
        if unit == "h"
        else timedelta(days=amount * _DURATION_UNIT_DAYS[unit])
    )
    return (now - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class GraphQuery:
    """Exactly one of ``search``/``filter`` is set; Graph rejects a request
    carrying both."""

    search: Optional[str] = None
    filter: Optional[str] = None


def translate_query(query: str, *, now: Optional[datetime] = None) -> GraphQuery:
    """Split a Gmail-shaped ``query`` into a Graph ``$search`` or ``$filter``.

    Raises ``ValueError`` when the query mixes a $filter-only concept
    (``is:unread``/``is:read``, ``newer_than:``/``older_than:``) with
    anything ``$search`` would otherwise carry (``from:``, ``subject:``, or
    bare text): Graph's mail endpoint errors if both parameters are sent
    together, so silently keeping one side would return a result set the
    caller never asked for.

    Every ``$search`` value is quoted and escaped per ``_graph_search_param``
    (#3021): ``from:``/``subject:`` are Graph KQL search-scoping keywords
    already, understood the same way inside the wrapping quotes, so quoting
    does not defeat them the way it defeats ``is:``/``newer_than:`` (which
    ``$search`` cannot express under any quoting and route to ``$filter``
    above instead).
    """
    now = now or datetime.now(timezone.utc)
    filters: List[str] = []

    def _is_sub(m: "re.Match[str]") -> str:
        value = m.group(1).lower()
        filters.append(f"isRead eq {'false' if value == 'unread' else 'true'}")
        return ""

    def _duration_sub(m: "re.Match[str]") -> str:
        op = m.group("op").lower()
        cutoff = _duration_to_cutoff(op, m.group("val"), now=now)
        comparator = "ge" if op == "newer_than" else "le"
        filters.append(f"receivedDateTime {comparator} {cutoff}")
        return ""

    remainder = _IS_RE.sub(_is_sub, query)
    remainder = _DURATION_RE.sub(_duration_sub, remainder)
    if filters:
        # Parentheses/brackets that wrapped a filter-only operator are syntax,
        # not a second search term. Keep rejecting actual text after removing
        # those wrappers, because Graph cannot combine $filter and $search.
        remainder = re.sub(r"[()\[\]{}]", " ", remainder)
    remainder = " ".join(remainder.split())

    if filters and remainder:
        raise ValueError(
            "search_messages: on Outlook, "
            + " and ".join(filters)
            + f" cannot be combined with {remainder!r} in one search. "
            "Microsoft Graph does not allow a date/unread filter and a "
            "text/from:/subject: search in the same request, so run them as "
            "two separate searches."
        )
    if filters:
        return GraphQuery(filter=" and ".join(filters))
    return GraphQuery(search=_graph_search_param(query))
