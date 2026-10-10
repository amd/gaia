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
