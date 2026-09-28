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
