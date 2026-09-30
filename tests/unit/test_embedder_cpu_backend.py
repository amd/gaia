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
    assert llamacpp_backend_for("Qwen3-30B-A3B-Instruct-2507-GGUF") is None
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
        json.dumps({"builtin.Qwen3-30B-A3B-Instruct-2507-GGUF": {"ctx_size": 65536}})
    )

    manager.write_config()

    options = json.loads(options_path.read_text())
    assert options[DEFAULT_EMBEDDING_MODEL] == {"llamacpp_backend": "cpu"}
    assert options["builtin.Qwen3-30B-A3B-Instruct-2507-GGUF"] == {"ctx_size": 65536}
