# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply the token limit cut off before any answer says so.

A reasoning model can spend all of ``max_tokens`` thinking. The provider then
returns empty content with ``finish_reason="length"``; ``AgentSDK.send`` used to
drop both that and the reasoning, handing callers a bare ``""``.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from gaia.chat.sdk import AgentConfig, AgentSDK


def _sdk(reply, finish_reason, reasoning=None, **config):
    client = MagicMock()
    client.generate.return_value = reply
    client.chat.return_value = reply
    client.get_last_usage.return_value = None
    client.get_last_finish_reason.return_value = finish_reason
    client.get_last_reasoning.return_value = reasoning
    with patch("gaia.chat.sdk.create_client", return_value=client):
        sdk = AgentSDK(config=AgentConfig(**config))
    sdk.get_stats = lambda: {}
    return sdk


@pytest.fixture
def sdk_warnings(caplog):
    caplog.set_level(logging.WARNING, logger="gaia.chat.sdk")
    return caplog


def _budget_warnings(caplog):
    return [r for r in caplog.records if "before writing any answer" in r.message]


def test_send_reports_a_reply_spent_entirely_on_reasoning(sdk_warnings):
    sdk = _sdk("", "length", reasoning="Thinking Process: 1. Analyze", max_tokens=200)

    response = sdk.send("Say hello quickly")

    assert response.text == ""
    assert response.finish_reason == "length"
    assert response.reasoning == "Thinking Process: 1. Analyze"
    [warning] = _budget_warnings(sdk_warnings)
    assert "max_tokens=200" in warning.message


def test_send_with_system_prompt_takes_the_same_path(sdk_warnings):
    sdk = _sdk("", "length", system_prompt="Be brief.", max_tokens=64)

    response = sdk.send("hi")

    assert response.finish_reason == "length"
    [warning] = _budget_warnings(sdk_warnings)
    assert "max_tokens=64" in warning.message


def test_per_call_max_tokens_is_the_one_named(sdk_warnings):
    sdk = _sdk("", "length", max_tokens=200)

    sdk.send("hi", max_tokens=32)

    [warning] = _budget_warnings(sdk_warnings)
    assert "max_tokens=32" in warning.message


def test_send_messages_warns_on_an_empty_cut_off_reply(sdk_warnings):
    sdk = _sdk("", "length", max_tokens=128)

    response = sdk.send_messages([{"role": "user", "content": "hi"}])

    assert response.finish_reason == "length"
    assert len(_budget_warnings(sdk_warnings)) == 1


@pytest.mark.parametrize(
    "reply, finish_reason",
    [
        ("Hello!", "stop"),
        ("Hello, I was cut o", "length"),  # truncated, but there is an answer
        ("", "stop"),
    ],
)
def test_no_warning_when_the_budget_did_not_eat_the_answer(
    sdk_warnings, reply, finish_reason
):
    response = _sdk(reply, finish_reason).send("hi")

    assert response.text == reply
    assert response.finish_reason == finish_reason
    assert _budget_warnings(sdk_warnings) == []


def test_send_stream_final_chunk_carries_finish_reason_and_reasoning():
    sdk = _sdk(iter(["<think>", "Thinking\n", "</think>"]), "length", "Thinking\n")

    chunks = list(sdk.send_stream("hi"))

    final = chunks[-1]
    assert final.is_complete
    assert final.finish_reason == "length"
    assert final.reasoning == "Thinking\n"
