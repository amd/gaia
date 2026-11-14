# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia prompt` prints a thinking model's answer, not its reasoning (#8032).

The Lemonade provider wraps a cloud model's reasoning in ``<think>`` tags.
`gaia llm` already leaves it out; these drive the real ``gaia.cli.main``
entry point because stdout is the thing under test.
"""

import sys

from gaia import cli


class _StubLLM:
    def __init__(self, chunks):
        self._chunks = chunks

    def generate(self, **kwargs):
        yield from self._chunks


def _prompt(monkeypatch, chunks):
    monkeypatch.setattr(cli, "create_client", lambda *a, **kw: _StubLLM(chunks))
    monkeypatch.setattr(
        sys,
        "argv",
        ["gaia", "prompt", "hi", "--max-tokens", "512", "--no-lemonade-check"],
    )
    try:
        cli.main()
    except SystemExit as exc:
        return 0 if exc.code is None else exc.code
    return 0


def test_streamed_reasoning_is_not_printed(monkeypatch, capsys):
    chunks = [
        "<think>",
        "The user wants ",
        "exactly that.",
        "</think>",
        "\n\n",
        "hel",
        "lo",
    ]

    assert _prompt(monkeypatch, chunks) == 0
    assert capsys.readouterr().out == "hello\n"


def test_reasoning_glued_to_the_answer_is_not_printed(monkeypatch, capsys):
    chunks = ["<think>plan</think>hello", "-rc24-once"]

    assert _prompt(monkeypatch, chunks) == 0
    assert capsys.readouterr().out == "hello-rc24-once\n"


def test_an_answer_without_reasoning_prints_as_streamed(monkeypatch, capsys):
    assert _prompt(monkeypatch, ["Hello ", "\n", "there"]) == 0
    assert capsys.readouterr().out == "Hello \nthere\n"


def test_reasoning_only_reply_names_max_tokens(monkeypatch, capsys):
    """A budget spent entirely reasoning says so instead of printing a blank line."""
    assert _prompt(monkeypatch, ["<think>", "still thinking"]) == 1

    out = capsys.readouterr().out
    assert "512-token budget" in out and "--max-tokens" in out
    assert "still thinking" not in out


def test_an_answer_that_mentions_think_prints_in_full(monkeypatch, capsys):
    """A quoted <think> mid-sentence is prose: nothing is cut and the exit is 0."""
    chunks = [
        "Qwen wraps its reasoning in <think>",
        " tags and closes them with </think>",
        " before the answer.",
    ]

    assert _prompt(monkeypatch, chunks) == 0
    assert capsys.readouterr().out == "".join(chunks) + "\n"


def test_a_quoted_tag_split_across_chunks_prints_in_full(monkeypatch, capsys):
    chunks = ["Use <th", "ink> to mark reasoning."]

    assert _prompt(monkeypatch, chunks) == 0
    assert capsys.readouterr().out == "Use <think> to mark reasoning.\n"
