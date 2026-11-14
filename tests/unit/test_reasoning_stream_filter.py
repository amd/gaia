# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A streamed reply's <think> block never reaches `gaia prompt`'s stdout (#8032)."""

import pytest

from gaia.llm.reasoning import ReasoningStreamFilter


def _shown(chunks):
    answer = ReasoningStreamFilter()
    return "".join(answer.feed(c) for c in chunks) + answer.flush()


REPLY = "<think>The user wants an exact reply. I should comply.</think>hello-rc24-once"


@pytest.mark.parametrize("size", [1, 2, 3, 5, 7, 8, 64])
def test_a_think_block_is_dropped_however_the_stream_splits_it(size):
    chunks = [REPLY[i : i + size] for i in range(0, len(REPLY), size)]
    assert _shown(chunks) == "hello-rc24-once"


def test_the_blank_line_after_the_block_is_not_printed():
    assert _shown(["<think>x</think>\n\n", "Answer"]) == "Answer"


def test_a_reply_with_no_reasoning_streams_as_it_comes():
    answer = ReasoningStreamFilter()
    assert answer.feed("Hello ") == "Hello "
    assert answer.feed("there") == "there"
    assert answer.flush() == ""


def test_a_leading_lone_close_tag_is_not_printed():
    """The template opened the block in the prompt and the reply has none."""
    assert _shown(["</th", "ink>\n", "42"]) == "42"


@pytest.mark.parametrize(
    "chunks",
    [
        ["Qwen wraps reasoning in <think> tags", " so clients can hide it."],
        ["Qwen wraps reasoning in <th", "ink> tags so clients can hide it."],
    ],
)
def test_a_tag_the_answer_mentions_is_printed(chunks):
    """Only a block the reply opens with is reasoning, as in split_reasoning."""
    assert (
        _shown(chunks) == "Qwen wraps reasoning in <think> tags so clients can hide it."
    )


def test_a_reply_cut_off_mid_thought_prints_nothing():
    assert _shown(["<think>still thinking about"]) == ""


def test_text_that_only_looks_like_a_tag_start_is_printed():
    assert _shown(["a < b and c <", "/div>"]) == "a < b and c </div>"


def test_gaia_prompt_prints_only_the_answer(monkeypatch, capsys):
    """The CLI client streams through the filter, not the raw chunks."""
    import asyncio

    from gaia import cli

    class _LLM:
        def generate(self, **_):
            yield from ["<think>reason", "ing</think>", "hello-rc24-once"]

    client = cli.GaiaCliClient.__new__(cli.GaiaCliClient)
    client.llm_client = _LLM()
    client.model = "m"
    client.max_tokens = 8
    client.calls_lemonade = True
    client.lemonade_url = None
    client.log = cli.GaiaCliClient.log

    async def drain():
        return [c async for c in client.prompt("hi")]

    assert asyncio.run(drain()) == ["hello-rc24-once"]
    assert capsys.readouterr().out == "hello-rc24-once"
