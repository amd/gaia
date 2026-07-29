# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drift guard: the Console-facing scope list in ``docs/connectors/google.mdx``
must stay in lock-step with ``GOOGLE_SPEC.available_scopes`` (issue #2602).

Modeled on ``test_catalog_docs_url.py`` (a catalog <-> ``docs/connectors/*.mdx``
cross-check with no optional-package dependency), not on
``test_email_scope_drift.py`` — that file does no markdown parsing and carries a
module-level ``pytest.importorskip("gaia_agent_email")`` that would silently
disable this guard in any CI job that doesn't install the email wheel.

The Console list lives in a fenced block bounded by
``<!-- google-scopes:start -->`` / ``<!-- google-scopes:end -->`` so this test
never scrapes freeform prose for scope strings — three of the ten scopes are
the bare words ``openid``, ``email``, and ``profile``, and "email" alone
appears repeatedly as ordinary prose elsewhere on the page.
"""

from __future__ import annotations

import re
from pathlib import Path

from gaia.connectors.catalog.google import GOOGLE_SPEC

_GOOGLE_MDX = Path(__file__).resolve().parents[3] / "docs" / "connectors" / "google.mdx"

_START_MARKER = "<!-- google-scopes:start -->"
_END_MARKER = "<!-- google-scopes:end -->"

_VALID_TIERS = {"Restricted", "Sensitive", "Non-sensitive"}

_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|")


def _scopes_block() -> str:
    text = _GOOGLE_MDX.read_text(encoding="utf-8")
    assert _START_MARKER in text and _END_MARKER in text, (
        f"docs/connectors/google.mdx is missing the {_START_MARKER!r} / "
        f"{_END_MARKER!r} markers around the Console scope table"
    )
    start = text.index(_START_MARKER) + len(_START_MARKER)
    end = text.index(_END_MARKER)
    assert start < end, "google-scopes markers appear out of order"
    return text[start:end]


def _parse_scope_rows() -> list[tuple[str, str]]:
    """Return ``(scope, tier)`` pairs for every data row in the scope table.

    Only rows whose first cell is a backtick-quoted literal are treated as
    data rows — this skips the header row and the ``---`` separator row
    without needing a markdown parser.
    """
    rows = []
    for line in _scopes_block().splitlines():
        match = _ROW_RE.match(line.strip())
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def test_registry_has_available_scopes():
    """Guard against a false-negative pass if ``available_scopes`` is empty."""
    assert GOOGLE_SPEC.available_scopes, "GOOGLE_SPEC.available_scopes is empty"


def test_docs_scope_table_is_not_empty():
    """Guard against a false-negative pass if the markers wrap nothing parseable."""
    assert _parse_scope_rows(), (
        "no scope rows parsed between the google-scopes markers in "
        "docs/connectors/google.mdx - check the table syntax"
    )


def test_documented_scopes_match_available_scopes():
    """The Console list must equal ``available_scopes`` exactly, not just contain it.

    9 of the 10 scopes are declared by some shipped agent; only ``drive.file``
    has zero consumers today. A containment check would let a genuinely new,
    undocumented scope slip through as long as the existing ones were still
    listed. Equality catches that.
    """
    documented = {scope for scope, _tier in _parse_scope_rows()}
    expected = set(GOOGLE_SPEC.available_scopes)
    assert documented == expected, (
        "docs/connectors/google.mdx Console scope list has drifted from "
        "GOOGLE_SPEC.available_scopes:\n"
        f"  documented but not in available_scopes: {sorted(documented - expected)}\n"
        f"  in available_scopes but not documented: {sorted(expected - documented)}"
    )


def test_every_documented_scope_has_exactly_one_valid_tier():
    """Every row must land in exactly one of the three published tiers.

    The set-equality test above only catches a missing/extra scope, not a
    scope whose tier cell is blank, typo'd, or duplicated across rows.
    """
    rows = _parse_scope_rows()
    scopes = [scope for scope, _tier in rows]
    assert len(scopes) == len(
        set(scopes)
    ), f"a scope appears more than once in the Console table: {scopes}"
    offenders = {scope: tier for scope, tier in rows if tier not in _VALID_TIERS}
    assert not offenders, (
        f"scopes with an unrecognized tier label (expected one of "
        f"{sorted(_VALID_TIERS)}): {offenders}"
    )
