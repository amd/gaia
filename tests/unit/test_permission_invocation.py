# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Permission previews disclose when their combined arguments are truncated."""

from gaia.ui.sse_translation import INVOCATION_TOTAL_CHARS, render_invocation


def test_combined_arguments_disclose_hidden_characters():
    args = {"first": "x" * 500, "second": "y" * 500, "third": "z" * 500}
    complete = ", ".join(f'{key}="{args[key]}"' for key in sorted(args))

    rendered = render_invocation(args)

    hidden = len(complete) - INVOCATION_TOTAL_CHARS
    assert rendered == (
        complete[:INVOCATION_TOTAL_CHARS] + f"… [+{hidden:,} more characters not shown]"
    )


def test_short_arguments_are_shown_in_full():
    assert render_invocation({"command": "git status"}) == 'command="git status"'


def test_a_value_with_quotes_stays_unambiguous():
    assert render_invocation({"content": 'print("hello")'}) == (
        "content='print(\"hello\")'"
    )
    assert render_invocation({"content": 'say "it\'s"'}) == (
        'content="say \\"it\'s\\""'
    )
    assert render_invocation({"content": "a\\\"b'c"}) == ('content="a\\\\\\"b\'c"')


def test_a_multi_line_value_keeps_its_lines_and_raw_backslashes():
    rendered = render_invocation(
        {"file_path": "C:\\notes\\list.txt", "content": 'line 1\nsay "it\'s"\n'}
    )
    assert rendered == (
        'file_path="C:\\notes\\list.txt"\ncontent:\n    line 1\n    say "it\'s"'
    )


def test_a_long_multi_line_value_discloses_hidden_lines():
    rendered = render_invocation({"content": "\n".join(f"l{i}" for i in range(20))})
    assert rendered.endswith("    l11\n    … [+8 more lines not shown]")
