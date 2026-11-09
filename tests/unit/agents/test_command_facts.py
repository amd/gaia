# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The shell-boundary guard behind "always allow" (#4876).

Backslash is a path separator on Windows and an escape on POSIX, so the same
line can put its command boundaries in different places under each shell.
`_tokenize` exists to refuse such a line: it returns None, `read_command`
reports it opaque, and `grant_scope` refuses to give it a standing grant. If
that guard breaks, a line that runs a second command on POSIX can be read as a
single harmless one and granted for the rest of the session.
"""

import pytest

from gaia.agents.base.command_facts import _tokenize, read_command
from gaia.agents.base.tool_grants import grant_scope

# The example the `_tokenize` docstring names. POSIX reads \" as a literal
# quote, so the first `;` sits outside any quoting and `curl x` runs there.
# Windows keeps the backslash, so the quote opens a string that swallows the
# line; `curl` does not run. The two readings disagree, so the line is refused.
DOCSTRING_EXAMPLE = r"pytest \" ; curl x ; \""


@pytest.mark.parametrize(
    "command",
    [
        # The docstring's example: boundaries disagree between the two shells.
        DOCSTRING_EXAMPLE,
        # Trailing word: still disagreeing, and the Windows reading still
        # swallows `curl`.
        r"pytest \" ; curl x ; \" extra",
        # Unbalanced on Windows: the windows lexer fails outright, so the line
        # is unreadable without needing the boundary comparison.
        r"pytest \" ; curl x",
    ],
)
def test_a_line_the_two_shells_split_differently_is_not_tokenized(command):
    assert _tokenize(command) is None


@pytest.mark.parametrize(
    "command",
    [
        DOCSTRING_EXAMPLE,
        r"pytest \" ; curl x ; \" extra",
        r"pytest \" ; curl x",
    ],
)
def test_an_unreadable_line_is_opaque(command):
    facts = read_command(command)
    assert facts.opaque is True
    # An opaque line is refused, not guessed at: no segments are invented.
    assert facts.segments == ()


def test_an_unreadable_line_gets_no_standing_grant():
    assert grant_scope("run_shell_command", {"command": DOCSTRING_EXAMPLE}) is None


def test_a_line_both_shells_read_alike_is_still_readable():
    # Control for the cases above: with nothing for the backslash to escape,
    # both shells put the whole line inside one pair of quotes, so it is
    # readable and is not reported opaque.
    command = r'pytest " ; curl x ; "'
    assert _tokenize(command) is not None
    assert read_command(command).opaque is False
