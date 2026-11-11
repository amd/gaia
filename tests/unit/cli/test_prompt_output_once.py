# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia prompt` prints the streamed answer once, never its result dict (#7186)."""

import sys

import pytest

from gaia import cli

ANSWER = "Hello! How can I help you today?"


@pytest.fixture
def stub_prompt(tmp_path, monkeypatch):
    home = tmp_path / "gaia_home"
    home.mkdir()
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(
        cli,
        "initialize_lemonade_for_agent",
        lambda agent, **kwargs: (True, "http://localhost:13305/api/v1"),
    )
    stats = {}

    class _StubClient:
        def __init__(
            self,
            model=None,
            max_tokens=512,
            show_stats=False,
            logging_level=None,
            base_url=None,
            use_claude=False,
            claude_model=None,
            **_,
        ):
            pass

        async def prompt(self, message):
            # The real client prints each chunk as it streams.
            for chunk in ("Hello! ", "How can I help you today?"):
                print(chunk, end="", flush=True)
                yield chunk

        def get_stats(self):
            return stats

    monkeypatch.setattr(cli, "GaiaCliClient", _StubClient)
    return stats


def _run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["gaia", "prompt", "Hello", *argv])
    cli.main()


def test_answer_printed_once_without_dict(stub_prompt, monkeypatch, capsys):
    _run(monkeypatch)
    out = capsys.readouterr().out

    assert out.count(ANSWER) == 1
    assert "{'response'" not in out
    assert out.endswith(ANSWER + "\n")


def test_stats_printed_readably(stub_prompt, monkeypatch, capsys):
    stub_prompt.update({"tokens_per_second": 42.5, "input_tokens": 7})
    _run(monkeypatch, "--stats")
    out = capsys.readouterr().out

    assert out.count(ANSWER) == 1
    assert "{" not in out
    assert "tokens_per_second: 42.5" in out
    assert "input_tokens: 7" in out
