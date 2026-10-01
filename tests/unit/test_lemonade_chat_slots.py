# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A llama.cpp chat model loads with a second slot and no host prompt cache.

With one slot, a side request carrying its own prompt (memory extraction)
first copied the conversation's KV cache to host RAM — 87s per switch for a
30K-token Qwen3-30B context on a Radeon 8060S — and the user's next turn
waited behind it.
"""

from unittest.mock import patch

import pytest
import responses

from gaia.llm.lemonade_client import CHAT_LLAMACPP_ARGS, LemonadeClient

BASE = "http://localhost:13305/api/v1"
CATALOG = [
    {
        "id": "Qwen3-30B-A3B-Instruct-2507-GGUF",
        "recipe": "llamacpp",
        "downloaded": True,
    },
    {"id": "gemma4-it-e2b-FLM", "recipe": "flm", "downloaded": True},
]


@pytest.fixture
def client():
    return LemonadeClient(base_url=BASE, verbose=False)


def _sent_load(client, model, **kwargs):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/models", json={"data": CATALOG})
        with patch.object(
            client, "_post_load_with_transient_retry", return_value={}
        ) as post:
            client.load_model(model, prompt=False, **kwargs)
    return post.call_args.args[1]


def test_a_llamacpp_chat_load_gets_two_slots_and_no_ram_cache(client):
    sent = _sent_load(client, "Qwen3-30B-A3B-Instruct-2507-GGUF", ctx_size=65536)
    assert sent["llamacpp_args"] == CHAT_LLAMACPP_ARGS
    for flag in ("--parallel 2", "--kv-unified", "--cache-ram 0"):
        assert flag in sent["llamacpp_args"]


@pytest.mark.parametrize(
    "model, kwargs",
    [
        # The caller's own flags win: the embedders tune their batch sizes.
        (
            "Qwen3-30B-A3B-Instruct-2507-GGUF",
            {"ctx_size": 8192, "llamacpp_args": "--ubatch-size 2048"},
        ),
        # Not a chat load: no context size was asked for.
        ("Qwen3-30B-A3B-Instruct-2507-GGUF", {}),
        ("gemma4-it-e2b-FLM", {"ctx_size": 32768}),
    ],
)
def test_other_loads_keep_their_flags(client, model, kwargs):
    sent = _sent_load(client, model, **kwargs)
    assert sent.get("llamacpp_args") == kwargs.get("llamacpp_args")
