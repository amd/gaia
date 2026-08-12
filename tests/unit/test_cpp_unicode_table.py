# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Guards the C++ chunker's uppercase table against drift from Python.

``cpp/src/unicode_upper_ranges.inc`` encodes ``str.isupper()`` so that the C++
port of ``RAGSDK._split_text_into_chunks`` cuts sections at the same lines as
this runtime. A hand-edited or stale table moves chunk boundaries for non-ASCII
headings, and the C++ fixture test cannot see it — the fixtures only cover the
code points they happen to contain.
"""

import re
import unicodedata
from pathlib import Path

import pytest

INC_PATH = (
    Path(__file__).resolve().parents[2] / "cpp" / "src" / "unicode_upper_ranges.inc"
)
RANGE_RE = re.compile(r"\{0x([0-9A-Fa-f]+),\s*0x([0-9A-Fa-f]+)\}")
VERSION_RE = re.compile(r"Unicode ([0-9]+\.[0-9]+\.[0-9]+)")


def _parse_ranges():
    text = INC_PATH.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    return [(int(lo, 16), int(hi, 16)) for lo, hi in RANGE_RE.findall(body)]


def _declared_version():
    match = VERSION_RE.search(INC_PATH.read_text(encoding="utf-8"))
    assert match, f"{INC_PATH} must name the Unicode release it was generated from"
    return match.group(1)


def test_ranges_are_sorted_and_disjoint():
    ranges = _parse_ranges()
    assert ranges, f"no ranges parsed from {INC_PATH}"
    for (lo, hi), (next_lo, _) in zip(ranges, ranges[1:]):
        assert lo <= hi, f"inverted range {lo:#x}-{hi:#x}"
        assert hi + 1 < next_lo, f"range {lo:#x}-{hi:#x} touches or overlaps the next"


def test_table_matches_this_interpreters_isupper():
    declared = _declared_version()
    if unicodedata.unidata_version != declared:
        pytest.skip(
            f"table is pinned to Unicode {declared}, interpreter has "
            f"{unicodedata.unidata_version} — regenerate "
            f"{INC_PATH.name} (see the header comment) to re-enable this check"
        )

    table = {cp for lo, hi in _parse_ranges() for cp in range(lo, hi + 1)}
    expected = {cp for cp in range(0x110000) if chr(cp).isupper()}
    missing = sorted(expected - table)[:10]
    extra = sorted(table - expected)[:10]
    assert table == expected, (
        f"uppercase table drifted from str.isupper(); "
        f"missing {[hex(c) for c in missing]}, extra {[hex(c) for c in extra]}. "
        f"Regenerate {INC_PATH.name} per its header comment."
    )
