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

from gaia.llm.lemonade_client import (
    CHAT_LLAMACPP_ARGS,
    CONVERSATION_SLOT,
    SIDE_SLOT,
    LemonadeAuthError,
    LemonadeClient,
)
from gaia.llm.providers.lemonade import LemonadeProvider

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


def test_each_slot_keeps_the_whole_memory_sized_window(client):
    # Without --kv-unified llama.cpp splits n_ctx across slots (n_ctx_seq =
    # n_ctx / n_parallel), silently halving the window the fit math sized.
    # Unified, both slots share one n_ctx-token pool: each sees the full window
    # and the KV memory is n_ctx tokens, which is what context_for_capacity charges.
    sent = _sent_load(client, "Qwen3-30B-A3B-Instruct-2507-GGUF", ctx_size=262144)
    flags = sent["llamacpp_args"].split()
    assert int(flags[flags.index("--parallel") + 1]) > 1
    assert "--kv-unified" in flags
    assert sent["ctx_size"] == 262144


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


def test_an_unreadable_catalog_loads_without_the_chat_flags(client, caplog):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, f"{BASE}/models", status=404)
        with patch.object(
            client, "_post_load_with_transient_retry", return_value={}
        ) as post:
            client.load_model(
                "Qwen3-30B-A3B-Instruct-2507-GGUF", ctx_size=65536, prompt=False
            )
    sent = post.call_args.args[1]
    assert sent == {
        "model_name": "Qwen3-30B-A3B-Instruct-2507-GGUF",
        "ctx_size": 65536,
    }
    assert "without the two-slot chat flags" in caplog.text


def test_an_auth_failure_on_the_catalog_still_fails_the_load(client):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, f"{BASE}/models", status=401)
        with patch.object(client, "_post_load_with_transient_retry") as post:
            with pytest.raises(LemonadeAuthError):
                client.load_model(
                    "Qwen3-30B-A3B-Instruct-2507-GGUF", ctx_size=65536, prompt=False
                )
    post.assert_not_called()


def test_a_known_recipe_skips_the_catalog_round_trip(client):
    client._model_metadata["Qwen3-30B-A3B-Instruct-2507-GGUF"] = {"recipe": "llamacpp"}
    with responses.RequestsMock() as rsps:  # any GET would raise ConnectionError
        with patch.object(
            client, "_post_load_with_transient_retry", return_value={}
        ) as post:
            client.load_model(
                "Qwen3-30B-A3B-Instruct-2507-GGUF", ctx_size=65536, prompt=False
            )
        assert len(rsps.calls) == 0
    assert post.call_args.args[1]["llamacpp_args"] == CHAT_LLAMACPP_ARGS


def _provider_sending(model):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
        }
        backend.return_value.cloud_model_provider.side_effect = lambda m: (
            "fireworks" if m.startswith("fireworks.") else None
        )
        provider = LemonadeProvider(model=model)
        provider.chat([{"role": "user", "content": "q"}])
        return backend.return_value.chat_completions.call_args.kwargs


def test_a_local_request_is_pinned_to_the_conversation_slot():
    sent = _provider_sending("Qwen3-30B-A3B-Instruct-2507-GGUF")
    assert sent["id_slot"] == CONVERSATION_SLOT


def test_a_cloud_request_carries_no_slot():
    assert "id_slot" not in _provider_sending("fireworks.glm-5p3")


def test_memory_extraction_runs_on_the_side_slot():
    from unittest.mock import MagicMock

    from gaia.agents.base.memory import MemoryMixin

    class Host(MemoryMixin):
        pass

    host = Host()
    host.chat = MagicMock()
    host.chat.send_messages.return_value = MagicMock(text="[]")
    assert host._extract_via_llm("My manager is Priya.", "Noted.", []) == []
    assert host.chat.send_messages.call_args.kwargs["id_slot"] == SIDE_SLOT
    assert SIDE_SLOT != CONVERSATION_SLOT


def _health(entry):
    return {"status": "ok", "all_models_loaded": [entry]}


@pytest.mark.parametrize(
    "entry, slots",
    [
        (
            {
                "model_name": "Qwen3-30B-A3B-Instruct-2507-GGUF",
                "launch_command": ["llama-server", "--port", "8001", "--parallel", "2"],
            },
            2,
        ),
        (
            {
                "model_name": "Qwen3-30B-A3B-Instruct-2507-GGUF",
                "recipe_options": {"llamacpp_args": CHAT_LLAMACPP_ARGS},
            },
            2,
        ),
        (
            {
                "model_name": "Qwen3-30B-A3B-Instruct-2507-GGUF",
                "launch_command": ["llama-server", "-np=3"],
            },
            3,
        ),
        # Loaded by another client without the chat flags.
        (
            {
                "model_name": "Qwen3-30B-A3B-Instruct-2507-GGUF",
                "launch_command": ["llama-server", "--port", "8001"],
                "recipe_options": {"ctx_size": 65536},
            },
            1,
        ),
        ({"model_name": "Gemma-4-E4B-it-GGUF", "launch_command": ["-np", "2"]}, 1),
    ],
)
def test_slot_count_reads_what_the_loaded_model_was_launched_with(client, entry, slots):
    with patch.object(client, "health_check", return_value=_health(entry)):
        assert client.slot_count("Qwen3-30B-A3B-Instruct-2507-GGUF") == slots


def _side_request(slots):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
        }
        backend.return_value.cloud_model_provider.return_value = None
        backend.return_value.slot_count.return_value = slots
        provider = LemonadeProvider(model="Qwen3-30B-A3B-Instruct-2507-GGUF")
        provider.chat([{"role": "user", "content": "q"}], id_slot=SIDE_SLOT)
        return backend.return_value.chat_completions.call_args.kwargs


def test_a_side_request_uses_the_side_slot_when_the_model_has_one():
    assert _side_request(2)["id_slot"] == SIDE_SLOT


def test_a_side_request_never_pins_a_slot_a_one_slot_model_lacks():
    # llama.cpp defers a request for a missing slot until the caller gives up.
    assert _side_request(1)["id_slot"] == CONVERSATION_SLOT
