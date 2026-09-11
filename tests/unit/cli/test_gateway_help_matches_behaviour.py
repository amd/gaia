# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia gateway auth` help text must not contradict what the command does.

The help said the token was "never stored on disk" long after `auth` started
remembering it in the OS credential store by default. That is the worst kind of
doc error for a credential: a user who reads it and expects the token to vanish
with the session gets one that persists, and never learns otherwise. Everything
else — the success message, the CLI reference — was already correct, so the help
string was the single outlier and nothing flagged the disagreement.

These tests pin the two together so the next edit to either has to face the
other.
"""

from __future__ import annotations

import pytest

from gaia.cli import build_parser


@pytest.fixture(scope="module")
def auth_help() -> str:
    """The `gateway auth` subparser's own help line, as `gaia gateway -h` shows it."""
    parser = build_parser()
    gateway = _subparser(parser, "gateway")
    return _choice_help(gateway, "auth")


def _subparser(parser, name: str):
    for action in parser._actions:  # noqa: SLF001 - argparse exposes no public API
        if hasattr(action, "choices") and action.choices and name in action.choices:
            return action.choices[name]
    raise AssertionError(f"no {name!r} subcommand on the parser")


def _choice_help(parser, name: str) -> str:
    for action in parser._actions:  # noqa: SLF001
        if not hasattr(action, "choices") or not action.choices:
            continue
        if name in action.choices:
            for choice in action._choices_actions:  # noqa: SLF001
                if choice.dest == name:
                    return choice.help or ""
    raise AssertionError(f"no help recorded for the {name!r} subcommand")


def test_help_does_not_claim_the_token_is_never_stored(auth_help: str):
    lowered = auth_help.lower()
    assert "never stored" not in lowered
    assert "not stored" not in lowered
    # "for this session" without qualification reads as the whole story, which
    # is what the original text got wrong.
    assert "for this session (" not in lowered


def test_help_says_where_the_token_goes_by_default(auth_help: str):
    """Storing a credential by default has to be visible before it happens."""
    assert "credential store" in auth_help.lower()


def test_help_names_the_flag_that_makes_it_session_only(auth_help: str):
    """The opt-out is only useful if the help that mentions the default names it."""
    assert "--no-remember" in auth_help


def test_the_opt_out_flag_exists_and_disables_storage(auth_help: str):
    """Guards the other direction: the help must not advertise a dead flag."""
    parser = build_parser()
    auth = _subparser(_subparser(parser, "gateway"), "auth")

    flags = {
        opt for action in auth._actions for opt in action.option_strings
    }  # noqa: SLF001
    assert "--no-remember" in flags

    args = parser.parse_args(["gateway", "auth"])
    assert args.no_remember is False, "storing must be the documented default"
    opted_out = parser.parse_args(["gateway", "auth", "--no-remember"])
    assert opted_out.no_remember is True
