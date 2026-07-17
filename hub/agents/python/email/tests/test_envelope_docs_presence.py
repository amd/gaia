# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mechanical docs guard for the #1892 context-window envelope section.

Purpose: a botched future conflict resolution that drops the envelope
section from CONTRACT.md and/or specification.html must fail CI instead of
silently vanishing. Deliberately a simple string-containment check — no
markdown/HTML parsing — so it stays cheap and robust to formatting changes
within the section.
"""

from __future__ import annotations

from pathlib import Path

HEADING = "Context-window envelope"
TARGET_TOKENS = ("16384", "16,384")
MAX_TOKENS = ("32768", "32,768")

# hub/agents/python/email/tests/test_envelope_docs_presence.py -> parents[1] =
# hub/agents/python/email (the package root).
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_MD = _PACKAGE_ROOT / "CONTRACT.md"
SPECIFICATION_HTML = _PACKAGE_ROOT / "specification.html"


def _section_after_heading(text: str, heading: str) -> str:
    """The doc text from the heading onward (to end of file — good enough
    for a simple containment check; the next top-level heading is not
    stripped out on purpose, so numbers anywhere after the heading count)."""
    idx = text.find(heading)
    assert idx != -1, f"heading {heading!r} not found"
    return text[idx:]


def test_contract_md_has_envelope_section_with_both_numbers():
    text = CONTRACT_MD.read_text(encoding="utf-8")
    assert HEADING in text, f"{CONTRACT_MD} is missing the {HEADING!r} heading"

    section = _section_after_heading(text, HEADING)
    assert any(
        tok in section for tok in TARGET_TOKENS
    ), f"{CONTRACT_MD} envelope section is missing the target token count (16384/16,384)"
    assert any(
        tok in section for tok in MAX_TOKENS
    ), f"{CONTRACT_MD} envelope section is missing the max token count (32768/32,768)"


def test_specification_html_has_envelope_section_with_both_numbers():
    text = SPECIFICATION_HTML.read_text(encoding="utf-8")
    assert HEADING in text, f"{SPECIFICATION_HTML} is missing the {HEADING!r} heading"

    section = _section_after_heading(text, HEADING)
    assert any(
        tok in section for tok in TARGET_TOKENS
    ), f"{SPECIFICATION_HTML} envelope section is missing the target token count (16384/16,384)"
    assert any(
        tok in section for tok in MAX_TOKENS
    ), f"{SPECIFICATION_HTML} envelope section is missing the max token count (32768/32,768)"


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
