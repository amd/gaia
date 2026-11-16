# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia hub` and the agent-publishing commands are hidden from help, not removed.

GAIA ships one agent, so browsing and publishing agents is no longer something
`gaia -h` offers (#5172). Scripts that already call these commands must keep
working, so each one still parses to the same action.
"""

from __future__ import annotations

import argparse

import pytest

from gaia.cli import (
    HIDDEN_AGENT_COMMANDS,
    HIDDEN_TOP_LEVEL_COMMANDS,
    _hide_subcommands,
    build_parser,
)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return build_parser()


def _help(parser: argparse.ArgumentParser) -> str:
    return parser.format_help()


def test_top_level_help_omits_hub(parser):
    text = _help(parser)
    assert "hub" not in _subparsers(parser).metavar.strip("{}").split(",")
    assert "Agent Hub" not in text
    assert "SUPPRESS" not in text
    # Hiding must not take visible commands with it.
    for visible in ("chat", "init", "agent", "daemon"):
        assert visible in _subparsers(parser).metavar


def test_agent_help_omits_publishing_commands(parser):
    agent = _subparsers(parser).choices["agent"]
    metavar = _subparsers(agent).metavar.strip("{}").split(",")
    for name in HIDDEN_AGENT_COMMANDS:
        assert name not in metavar
    text = _help(agent)
    assert "SUPPRESS" not in text
    assert "publish " not in text  # "a published agent" is fine
    for visible in ("test", "export", "import", "install", "list"):
        assert visible in metavar


@pytest.mark.parametrize(
    "argv, attr, expected",
    [
        (["hub", "list"], "hub_action", "list"),
        (["hub", "install", "email", "--trust"], "hub_action", "install"),
        (["hub", "uninstall", "email"], "hub_action", "uninstall"),
        (["agent", "pack"], "agent_action", "pack"),
        (["agent", "publish"], "agent_action", "publish"),
        (["agent", "login", "--hub"], "agent_action", "login"),
        (["agent", "version", "patch"], "agent_action", "version"),
        (["agent", "init", "my-agent"], "agent_action", "init"),
    ],
)
def test_hidden_commands_still_parse(parser, argv, attr, expected):
    args = parser.parse_args(argv)
    assert getattr(args, attr) == expected


def test_hidden_command_help_still_renders(parser, capsys):
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["hub", "install", "-h"])
    assert exc.value.code == 0
    assert "agent_id" in capsys.readouterr().out


def test_hiding_an_unknown_command_fails_loudly():
    p = argparse.ArgumentParser()
    subs = p.add_subparsers(dest="action")
    subs.add_parser("real", help="exists")
    with pytest.raises(ValueError, match="typo"):
        _hide_subcommands(subs, ["typo"])


def test_hidden_lists_name_real_commands(parser):
    top = _subparsers(parser)
    assert set(HIDDEN_TOP_LEVEL_COMMANDS) <= set(top.choices)
    assert set(HIDDEN_AGENT_COMMANDS) <= set(_subparsers(top.choices["agent"]).choices)
