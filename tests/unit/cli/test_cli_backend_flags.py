"""Backend flags are accepted only by the commands that honour them (#4217)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from gaia.cli import build_parser

# Minimal valid argv per command; the baseline test proves each parses on its own.
_NON_BACKEND_COMMANDS = [
    ["kill", "--port", "1"],
    ["download"],
    ["stats"],
    ["test", "--test-type", "tts-preprocessing"],
    ["youtube"],
    ["report"],
    ["perf-vis", "server.log"],
    ["schedule"],
    ["telegram"],
    ["slack"],
    ["mcp"],
    ["mcp", "start"],
    ["install"],
    ["uninstall"],
    ["eval"],
    ["llm", "hi"],
    ["api", "status"],
    ["email"],
]

_CLAUDE_AND_LOOP_FLAGS = [
    ["--use-claude"],
    ["--claude-model", "claude-sonnet-5"],
    ["--max-steps", "3"],
    ["--list-tools"],
    ["--stream"],
]


def _assert_rejected(argv, capsys):
    # Exit 2 is enough: the baseline test shows argv parses without the flag. A
    # flag's value can surface as "invalid choice" on commands with subcommands.
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2
    capsys.readouterr()


@pytest.mark.parametrize("argv", _NON_BACKEND_COMMANDS, ids=" ".join)
def test_baseline_argv_parses(argv):
    build_parser().parse_args(argv)


@pytest.mark.parametrize("flag", _CLAUDE_AND_LOOP_FLAGS, ids=lambda f: f[0])
@pytest.mark.parametrize("argv", _NON_BACKEND_COMMANDS, ids=" ".join)
def test_non_agent_commands_reject_backend_flags(argv, flag, capsys):
    if argv[0] == "api" and flag == ["--stream"]:
        pytest.skip("argparse reads --stream as an abbreviation of api's --streaming")
    _assert_rejected(argv + flag, capsys)


@pytest.mark.parametrize(
    "argv",
    [
        ["kill", "--port", "1", "--base-url", "http://h:1/api/v1"],
        ["download", "--model", "m"],
        ["eval", "--model", "m"],
        ["report", "--trace"],
        ["mcp", "--base-url", "http://h:1/api/v1"],
    ],
    ids=" ".join,
)
def test_unread_model_and_base_url_rejected(argv, capsys):
    _assert_rejected(argv, capsys)


@pytest.mark.parametrize(
    "argv",
    [
        ["prompt", "hi", "--max-steps", "3"],
        ["prompt", "hi", "--list-tools"],
        ["prompt", "hi", "--trace"],
        ["prompt", "hi", "--stream"],
        ["talk", "--max-steps", "3"],
        ["talk", "--list-tools"],
    ],
    ids=" ".join,
)
def test_agent_loop_flags_rejected_outside_chat(argv, capsys):
    _assert_rejected(argv, capsys)


@pytest.mark.parametrize(
    "argv, expected",
    [
        (
            ["chat", "--use-claude", "--claude-model", "claude-x", "--trace"]
            + ["--max-steps", "3", "--list-tools", "--stream", "--model", "m"]
            + ["--base-url", "http://h:1/api/v1"],
            {"use_claude": True, "max_steps": 3, "list_tools": True, "trace": True},
        ),
        (
            ["prompt", "hi", "--use-claude", "--base-url", "http://h:1/api/v1"],
            {"use_claude": True, "base_url": "http://h:1/api/v1"},
        ),
        (["talk", "--use-claude", "--model", "m"], {"use_claude": True, "model": "m"}),
        (["llm", "hi", "--model", "m", "--base-url", "u"], {"model": "m"}),
        (["email", "--model", "m", "--base-url", "u", "--trace"], {"trace": True}),
        (["api", "start", "--base-url", "u"], {"base_url": "u"}),
        (["mcp", "start", "--base-url", "u"], {"base_url": "u"}),
        (["stats", "--base-url", "u"], {"base_url": "u"}),
    ],
    ids=lambda v: " ".join(v) if isinstance(v, list) else "",
)
def test_commands_keep_the_flags_they_read(argv, expected):
    args = build_parser().parse_args(argv)
    for key, value in expected.items():
        assert getattr(args, key) == value


def _run_prompt(monkeypatch, capsys, *extra):
    import gaia.cli as cli_mod

    llm = MagicMock()
    llm.generate.return_value = iter(["ok"])
    create = MagicMock(return_value=llm)
    monkeypatch.setattr(cli_mod, "create_client", create)
    monkeypatch.setattr(cli_mod, "initialize_lemonade_for_agent", MagicMock())
    monkeypatch.setattr(
        sys,
        "argv",
        ["gaia", "prompt", "hi", "--model", "m", "--no-lemonade-check", *extra],
    )
    cli_mod.main()
    capsys.readouterr()
    return create, llm


def test_prompt_passes_base_url_to_client(monkeypatch, capsys):
    create, _ = _run_prompt(
        monkeypatch, capsys, "--base-url", "http://remote:13305/api/v1"
    )
    create.assert_called_once_with(
        "lemonade", model="m", base_url="http://remote:13305/api/v1"
    )


def test_prompt_use_claude_builds_claude_client(monkeypatch, capsys):
    create, llm = _run_prompt(
        monkeypatch, capsys, "--use-claude", "--claude-model", "claude-sonnet-5"
    )
    create.assert_called_once_with("claude", model="claude-sonnet-5")
    assert llm.generate.call_args.kwargs["model"] == "claude-sonnet-5"
