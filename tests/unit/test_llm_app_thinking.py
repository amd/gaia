# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""`gaia llm` must get an answer out of a thinking model.

With thinking on, Qwen3.6 spent the default 512-token budget reasoning and
`gaia llm "hello"` printed a raw <think> block and no answer.
"""

from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME


@pytest.fixture
def app():
    with patch("gaia.apps.llm.app.create_client") as create_client:
        client = MagicMock()
        client.generate.return_value = "ok"
        create_client.return_value = client
        from gaia.apps.llm.app import LlmApp

        yield LlmApp(), client


def test_thinking_model_is_asked_not_to_think(app):
    llm, client = app
    llm.query("hello", model=LARGE_DEFAULT_MODEL_NAME)
    kwargs = client.generate.call_args.kwargs
    assert kwargs["chat_template_kwargs"] == {"enable_thinking": False}
    assert kwargs["max_tokens"] == 512


def test_default_model_resolves_from_config(app):
    llm, client = app
    with patch(
        "gaia.apps.llm.app.resolve_default_chat_model",
        return_value=LARGE_DEFAULT_MODEL_NAME,
    ):
        llm.query("hello")
    kwargs = client.generate.call_args.kwargs
    assert kwargs["chat_template_kwargs"] == {"enable_thinking": False}
    assert kwargs["model"] is None


def test_model_without_a_thinking_switch_sends_nothing(app):
    llm, client = app
    llm.query("hello", model=DEFAULT_MODEL_NAME)
    assert "chat_template_kwargs" not in client.generate.call_args.kwargs


def test_caller_choice_wins(app):
    llm, client = app
    llm.query(
        "hello",
        model=LARGE_DEFAULT_MODEL_NAME,
        chat_template_kwargs={"enable_thinking": True},
    )
    kwargs = client.generate.call_args.kwargs
    assert kwargs["chat_template_kwargs"] == {"enable_thinking": True}


def test_streamed_reasoning_is_not_printed(capsys):
    """A cloud model's reasoning arrives wrapped in <think>; only the answer prints."""
    from gaia.apps.llm import app as llm_app

    chunks = ["<think>", "The user wants ", "PONG.", "</think>", "PO", "NG"]
    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = iter(chunks)
        answer = llm_app.main(query="ping", stream=True)

    assert answer == "PONG"
    out = capsys.readouterr().out
    assert "think" not in out and "The user wants" not in out


def test_reasoning_glued_to_the_answer_is_dropped():
    from gaia.apps.llm.app import _without_reasoning

    chunks = ["<think>plan", " more</think>Answer", " <think>x</think>end"]
    assert "".join(_without_reasoning(chunks)) == "Answer end"


def test_non_streamed_reasoning_is_not_printed(capsys):
    from gaia.apps.llm import app as llm_app

    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = "<think>hmm</think>PONG"
        answer = llm_app.main(query="ping", stream=False)

    assert answer == "PONG"
    assert "hmm" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "chunks",
    [
        ["<think>", "plan...", "</think>"],
        ["<think>", "cut off mid-thought"],
        ["<think>", "plan", "</think>", "\n\n", "  "],
    ],
)
def test_streamed_reasoning_only_fails_loudly(chunks):
    """A budget spent entirely reasoning names --max-tokens instead of a blank line."""
    from gaia.apps.llm import app as llm_app

    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = iter(chunks)
        with pytest.raises(RuntimeError, match="--max-tokens"):
            llm_app.main(query="ping", stream=True, max_tokens=512)


@pytest.mark.parametrize(
    "raw", ["<think>hmm</think>", "<think>never closed", "<think>a</think> \n"]
)
def test_non_streamed_reasoning_only_fails_loudly(raw):
    from gaia.apps.llm import app as llm_app

    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = raw
        with pytest.raises(RuntimeError, match="512-token budget"):
            llm_app.main(query="ping", stream=False, max_tokens=512)


@pytest.mark.parametrize("stream", [True, False])
def test_an_empty_reply_without_reasoning_is_not_blamed_on_reasoning(stream):
    from gaia.apps.llm import app as llm_app

    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = iter([""]) if stream else ""
        assert llm_app.main(query="ping", stream=stream) == ""


def test_streamed_answer_after_reasoning_has_no_leading_blank_lines(capsys):
    """Streamed and non-streamed paths trim the answer the same way."""
    from gaia.apps.llm import app as llm_app

    chunks = ["<think>", "x", "</think>", "\n\n", "  PO", "NG"]
    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = iter(chunks)
        assert llm_app.main(query="ping", stream=True) == "PONG"


def test_whitespace_inside_the_answer_is_kept():
    from gaia.apps.llm import app as llm_app

    chunks = ["Hello", " ", "\n", "world"]
    with patch.object(llm_app, "LlmApp") as cls:
        cls.return_value.query.return_value = iter(chunks)
        assert llm_app.main(query="ping", stream=True) == "Hello \nworld"


def test_angle_brackets_that_are_not_think_tags_pass_through():
    from gaia.apps.llm.app import _without_reasoning

    chunks = ["a < b", " and <thinking> is", " not </think> a tag"]
    assert (
        "".join(_without_reasoning(chunks))
        == "a < b and <thinking> is not </think> a tag"
    )
