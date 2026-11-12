# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The command gate reads a flag the way the CLI's own parser does.

Two spellings used to slip past it: a value-taking flag written ``--flag=``
with nothing after the ``=``, and git's abbreviated long options and clustered
short options.
"""

from __future__ import annotations

import shlex

import pytest

from gaia.skills.binaries import (
    ALLOW,
    BINARY_POLICIES,
    CONFIRM,
    REFUSE,
    _split_flag,
    classify_invocation,
)


def _decide(command: str):
    argv = shlex.split(command)
    return classify_invocation(BINARY_POLICIES[argv[0]], argv)


# --- empty attached values ---------------------------------------------------


@pytest.mark.parametrize(
    ("token", "kwargs", "expected"),
    [
        ("--jq=", {}, ("--jq", "")),
        ("--jq=.x", {}, ("--jq", ".x")),
        ("--jq", {}, ("--jq", None)),
        ("-X=", {}, ("-X", "")),
        ("-X=GET", {}, ("-X", "GET")),
        ("-XGET", {}, ("-X", "GET")),
        ("-X", {}, ("-X", None)),
        ("-exec=", {"single_dash_long": True}, ("-exec", "")),
        ("-exec", {"single_dash_long": True}, ("-exec", None)),
    ],
)
def test_split_flag_keeps_an_empty_attached_value(token, kwargs, expected):
    assert _split_flag(token, **kwargs) == expected


@pytest.mark.parametrize(
    "command",
    [
        "gh api --jq= --input body.json repos/o/r/issues",
        "gh api --method= -XDELETE repos/o/r",
        "gh api -X= DELETE repos/o/r",
        "git commit -m= --amend",
        "git commit --message= --no-verify",
        "pytest -k= -p evil",
        "python -W= -c print(1)",
    ],
)
def test_an_empty_attached_value_is_refused(command):
    decision = _decide(command)
    assert decision.outcome == REFUSE
    assert "needs a value" in decision.message


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        ("gh api --jq .x repos/o/r/issues", ALLOW),
        ("gh api --jq=.x repos/o/r/issues", ALLOW),
        ("gh api --method=GET repos/o/r", ALLOW),
        ("gh api -XGET repos/o/r", ALLOW),
        ("git commit -m fix", CONFIRM),
        ("git commit --message=fix", CONFIRM),
        ("pytest -k foo tests/unit", ALLOW),
    ],
)
def test_a_flag_with_its_value_still_runs(command, outcome):
    assert _decide(command).outcome == outcome


# --- git abbreviations and short clusters ------------------------------------


@pytest.mark.parametrize(
    ("command", "read_as"),
    [
        ("git diff --ext", "--ext-diff"),
        ("git diff --textc", "--textconv"),
        ("git log --outp=out.txt", "--output"),
        ("git commit --amen -m x", "--amend"),
        ("git commit --no-veri -m x", "--no-verify"),
        ("git checkout --forc main", "--force"),
        ("git branch --del topic", "--delete"),
        ("git commit -an -m x", "-n"),
        ("git checkout -qf main", "-f"),
        ("git branch -vD topic", "-D"),
        ("git grep -nO foo", "-O"),
        ("git add -Ap", "-p"),
    ],
)
def test_git_denied_flags_are_matched_in_every_spelling_git_accepts(command, read_as):
    decision = _decide(command)
    assert decision.outcome == REFUSE
    assert read_as in decision.message


@pytest.mark.parametrize(
    ("command", "outcome"),
    [
        ("git diff --text", ALLOW),
        ("git diff --exit-code", ALLOW),
        ("git diff --no-ext-diff", ALLOW),
        ("git log --filter=blob:none", ALLOW),
        ("git log --output-indicator-new=+", ALLOW),
        ("git log --oneline -n5", ALLOW),
        ("git grep -n foo", ALLOW),
        ("git branch -v", ALLOW),
        ("git commit -am fix", CONFIRM),
        # Letters after a value-taking flag are its value, not more flags.
        ("git commit -amnote", CONFIRM),
        ("git stash push -mfix", CONFIRM),
        ("git checkout main", CONFIRM),
        ("git checkout -b topic", CONFIRM),
    ],
)
def test_ordinary_git_flags_still_run(command, outcome):
    assert _decide(command).outcome == outcome


def test_exact_denied_flag_message_is_unchanged():
    decision = _decide("git diff --ext-diff")
    assert decision.outcome == REFUSE
    assert decision.message.startswith("'git diff --ext-diff' is not allowed: ")


def test_prefix_matching_is_limited_to_git():
    """pflag (gh) has no abbreviations, so a prefix there is a different flag."""
    assert not BINARY_POLICIES["gh"].abbreviated_options
    assert BINARY_POLICIES["git"].abbreviated_options
