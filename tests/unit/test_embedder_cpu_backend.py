# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The embedder runs on llama.cpp's CPU backend; chat models keep the GPU's fast path.

Vulkan's cooperative-matrix path crashes llama-server as it loads the embedder
on AMD Radeon iGPUs (#1831). Disabling it globally fixed the crash but halved
every chat model's prompt speed (Qwen3 30B-A3B on a Radeon 8060S: 683 vs 1293
prompt tokens/s), so only the embedder moves off it.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDER_LLAMACPP_ARGS,
    LemonadeClient,
    llamacpp_backend_for,
)


@pytest.fixture
def not_macos(monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Windows")


def test_the_embedder_loads_on_cpu_under_either_name(not_macos):
    assert llamacpp_backend_for(DEFAULT_EMBEDDING_MODEL) == "cpu"
    assert llamacpp_backend_for("embeddinggemma-300m-GGUF") == "cpu"


def test_chat_models_keep_the_default_backend(not_macos):
    assert llamacpp_backend_for("Qwen3.6-35B-A3B-GGUF") is None
    assert llamacpp_backend_for("Gemma-4-E4B-it-GGUF") is None


def test_macos_uses_metal_for_everything(monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Darwin")
    assert llamacpp_backend_for(DEFAULT_EMBEDDING_MODEL) is None


def test_the_load_request_names_the_cpu_backend(not_macos):
    client = LemonadeClient(verbose=False)
    sent = {}

    def fake_post(url, request_data, *args, **kwargs):
        sent.update(request_data)
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"status": "success"}
        return response

    with patch.object(client, "_post_load_with_transient_retry", fake_post):
        client._load_model_leased(DEFAULT_EMBEDDING_MODEL, ctx_size=8192)

    assert sent["llamacpp_backend"] == "cpu"


def test_embedded_lemonade_pins_the_embedder_for_auto_loads(tmp_path, not_macos):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    manager = EmbeddedLemonade(home=tmp_path)
    options_path = manager.config_dir / "recipe_options.json"
    options_path.parent.mkdir(parents=True)
    options_path.write_text(
        json.dumps({"builtin.Qwen3.6-35B-A3B-GGUF": {"ctx_size": 65536}})
    )

    manager.write_config()

    options = json.loads(options_path.read_text())
    assert options[DEFAULT_EMBEDDING_MODEL] == {
        "llamacpp_backend": "cpu",
        "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
    }
    assert options["builtin.Qwen3.6-35B-A3B-GGUF"] == {"ctx_size": 65536}


def test_an_unreadable_options_file_fails_loudly(tmp_path, not_macos):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedLemonadeError

    manager = EmbeddedLemonade(home=tmp_path)
    options_path = manager.config_dir / "recipe_options.json"
    options_path.parent.mkdir(parents=True)
    options_path.write_text("[]")

    with pytest.raises(EmbeddedLemonadeError, match="not a JSON object"):
        manager.write_config()


@pytest.fixture
def fresh_pins(monkeypatch):
    monkeypatch.setattr(lemonade_client, "_PINNED_BACKENDS", set())


def _recording_client(calls):
    client = LemonadeClient(verbose=False)

    def fake_send(method, url, data=None, **kwargs):
        calls.append((url.rsplit("/", 1)[-1], dict(data or {})))
        if url.endswith("/embeddings"):
            return {"data": [{"embedding": [0.0, 1.0]}]}
        return {"status": "success"}

    client._send_request = fake_send
    return client


def test_a_load_of_the_embedder_saves_its_backend(not_macos, fresh_pins):
    """Lemonade auto-loads the embedder for ``/embeddings`` from saved options."""
    calls = []
    client = _recording_client(calls)

    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert calls == [
        (
            "load",
            {
                "model_name": DEFAULT_EMBEDDING_MODEL,
                "llamacpp_backend": "cpu",
                "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
                "save_options": True,
            },
        )
    ]


def test_the_first_embedding_pins_the_backend_once(not_macos, fresh_pins):
    """A server GAIA did not configure would auto-load the embedder on Vulkan."""
    calls = []
    client = _recording_client(calls)
    client.model = "Qwen3.6-35B-A3B-GGUF"

    client.embeddings("one", model=DEFAULT_EMBEDDING_MODEL)
    client.embeddings("two", model=DEFAULT_EMBEDDING_MODEL)

    assert [endpoint for endpoint, _ in calls] == ["load", "embeddings", "embeddings"]
    assert calls[0][1]["llamacpp_backend"] == "cpu"
    assert calls[0][1]["llamacpp_args"] == EMBEDDER_LLAMACPP_ARGS
    assert calls[0][1]["save_options"] is True
    assert client.model == "Qwen3.6-35B-A3B-GGUF"


def test_other_embedders_are_not_loaded_first(not_macos, fresh_pins):
    calls = []
    client = _recording_client(calls)

    client.embeddings("one", model="nomic-embed-text-v2-moe-GGUF")

    assert [endpoint for endpoint, _ in calls] == ["embeddings"]


def test_embedded_lemonade_keeps_an_existing_ubatch(tmp_path, not_macos):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    manager = EmbeddedLemonade(home=tmp_path)
    options_path = manager.config_dir / "recipe_options.json"
    options_path.parent.mkdir(parents=True)
    custom = "--ubatch-size 4096 --split-mode none"
    options_path.write_text(
        json.dumps({DEFAULT_EMBEDDING_MODEL: {"llamacpp_args": custom}})
    )

    manager.write_config()

    saved = json.loads(options_path.read_text())[DEFAULT_EMBEDDING_MODEL]
    assert saved["llamacpp_args"] == custom


def test_an_explicit_embedder_args_string_is_sent_unchanged(not_macos, fresh_pins):
    calls = []
    client = _recording_client(calls)

    client.load_model(
        DEFAULT_EMBEDDING_MODEL,
        prompt=False,
        llamacpp_args="--ubatch-size 2048 --split-mode none",
    )

    assert calls[0][1]["llamacpp_args"] == "--ubatch-size 2048 --split-mode none"


def test_chat_models_get_no_embedder_ubatch(not_macos, fresh_pins):
    calls = []
    client = _recording_client(calls)

    client.load_model("Qwen3.6-35B-A3B-GGUF", prompt=False)

    assert "llamacpp_args" not in calls[0][1]
