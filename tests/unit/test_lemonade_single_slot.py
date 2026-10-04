# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A llama.cpp chat model loads with GAIA's slot count, whatever Lemonade's default.

Lemonade before v11.8.0 passed no ``--parallel``, so llama-server picked its own
slot count over one unified KV pool, each slot promised the whole window, and
concurrent requests failed with "Context size has been exceeded"
(lemonade-sdk/lemonade#3276). GAIA names the slots itself and runs one request
at a time per local model (``test_lemonade_chat_slots.py``).
"""

from unittest.mock import patch

import pytest
import responses

from gaia.llm.lemonade_client import (
    CHAT_LLAMACPP_ARGS,
    LemonadeAuthError,
    LemonadeClient,
)

BASE = "http://localhost:13305/api/v1"
CATALOG = [
    {"id": "Qwen3.6-35B-A3B-GGUF", "recipe": "llamacpp", "downloaded": True},
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


def test_a_llamacpp_chat_load_names_its_slot_count(client):
    sent = _sent_load(client, "Qwen3.6-35B-A3B-GGUF", ctx_size=262144)
    assert sent["llamacpp_args"] == CHAT_LLAMACPP_ARGS
    assert "--parallel 2" in sent["llamacpp_args"]
    assert sent["ctx_size"] == 262144


@pytest.mark.parametrize(
    "model, kwargs",
    [
        # The caller's own flags win: the embedders tune their batch sizes.
        ("Qwen3.6-35B-A3B-GGUF", {"ctx_size": 8192, "llamacpp_args": "-ub 2048"}),
        # Not a chat load: no context size was asked for.
        ("Qwen3.6-35B-A3B-GGUF", {}),
        # Not llama.cpp.
        ("gemma4-it-e2b-FLM", {"ctx_size": 32768}),
    ],
)
def test_other_loads_keep_their_flags(client, model, kwargs):
    sent = _sent_load(client, model, **kwargs)
    assert sent.get("llamacpp_args") == kwargs.get("llamacpp_args")


def test_an_unreadable_catalog_loads_without_the_chat_flags(client, caplog):
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{BASE}/models", status=500, body="boom")
        with patch.object(
            client, "_post_load_with_transient_retry", return_value={}
        ) as post:
            client.load_model("Qwen3.6-35B-A3B-GGUF", ctx_size=65536, prompt=False)
    assert "llamacpp_args" not in post.call_args.args[1]
    assert "Could not read the model catalog" in caplog.text


def test_an_auth_failure_on_the_catalog_still_fails_the_load(client):
    with patch.object(client, "_model_recipe", side_effect=LemonadeAuthError("401")):
        with pytest.raises(LemonadeAuthError):
            client.load_model("Qwen3.6-35B-A3B-GGUF", ctx_size=65536, prompt=False)
