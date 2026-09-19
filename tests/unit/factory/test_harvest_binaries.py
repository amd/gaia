# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the shell tokenizer behind the binary and switch tables.

SKILL.md calls binary frequency "the single richest signal" in the report, and
before #3935 roughly 10% of its invocations were not binaries: a regex split
shredded quoted arguments, so ``grep -n "def .*("`` contributed a command
called ``def``. The two tables also disagreed — ``assert`` was counted 46 times
in one and 1,725 in the other — because only one of them stripped inline
script bodies.
"""

from collections import Counter

import pytest

from gaia.factory.harvest.report import (
    _binaries,
    _segment_head,
    _split_segments,
    _strip_inline_script,
)


@pytest.mark.parametrize(
    "cmd,expected",
    [
        # The canonical regression: | and ) live inside the grep pattern.
        ('grep -n "def .*(" f.py | head -40', ["grep", "head"]),
        ('grep -E "a|b" x', ["grep"]),
        ("echo 'a;b' ; ls", ["echo", "ls"]),
        # Real operators outside quotes still split.
        ("cd /x && git status", ["cd", "git"]),
        ("a || b", ["a", "b"]),
        # A substitution runs a real process.
        ("ls $(pwd)", ["ls", "pwd"]),
        # Wrappers slide through to the command they wrap.
        ("sudo env FOO=1 git push", ["git"]),
    ],
)
def test_binaries(cmd, expected):
    assert _binaries(cmd) == expected


@pytest.mark.parametrize(
    "cmd",
    [
        'python -c "import os; assert True"',
        'python3.12 -c "def f(): pass"',
        "python3 - <<'EOF'\ndef f():\n    import sys\nEOF",
        "cat > x.py <<EOF\nclass A:\n    pass\nEOF",
    ],
)
def test_inline_script_bodies_contribute_no_binaries(cmd):
    """Source text is data. Parsing past it invents commands out of keywords."""
    found = _binaries(cmd)
    for word in ("def", "import", "assert", "class", "pass"):
        assert word not in found, f"{word} leaked from {cmd!r}"


def test_versioned_interpreter_is_recognised():
    """python3.12 -c used to miss the marker, leaking its whole script body."""
    assert "def" not in _binaries('python3.12 -c "def f(): pass"')
    assert _strip_inline_script('python3.12 -c "x"').endswith("-c ")


def test_loop_variable_is_not_a_binary():
    """`for n in 3697; do gh pr view $n` reported a binary called `n`."""
    found = _binaries("for n in 3697 3698; do gh pr view $n; done")
    assert "n" not in found
    assert "gh" in found


def test_variable_expanded_command_is_unknowable():
    """$S/drive.sh keys d reported `keys` — the script's first argument."""
    assert _segment_head("$S/drive.sh keys d") is None
    assert "keys" not in _binaries("$S/drive.sh keys d; sleep 2")


def test_quotes_survive_segmentation():
    segs = _split_segments('grep "a|b" x')
    assert len(segs) == 1 and segs[0] == 'grep "a|b" x'


def test_escaped_quote_inside_double_quotes():
    assert _split_segments('echo "a\\"|b"') == ['echo "a\\"|b"']


def test_single_quotes_make_substitution_literal():
    """`$(` inside single quotes is text, not a process."""
    assert _binaries("echo '$(date)'") == ["echo"]


def test_both_tables_see_the_same_segments():
    """The switch table and the binary table must not disagree (#3935).

    They share _strip_inline_script + _split_segments precisely so that a
    binary's invocation count and its segment count cannot drift apart.
    """
    cmds = [
        'grep -n "def .*(" a.py | head -40',
        "cd /x && git status --short",
        'python -c "assert 1"',
        "for n in 1 2; do gh pr view $n; done",
    ]
    from_binaries = Counter(b for c in cmds for b in _binaries(c))
    from_segments = Counter()
    for c in cmds:
        for seg in _split_segments(_strip_inline_script(c)):
            parsed = _segment_head(seg)
            if parsed:
                from_segments[parsed[0]] += 1
    assert from_binaries == from_segments
