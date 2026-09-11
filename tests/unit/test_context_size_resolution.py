# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep startup and local-model reload requests on the same context window."""

# pylint: disable=protected-access

import json
from copy import deepcopy
from unittest.mock import patch

import pytest
import responses

from gaia.cli import initialize_lemonade_for_agent
from gaia.config import GaiaConfig
from gaia.llm.lemonade_client import (
    MODELS,
    LemonadeClient,
    LemonadeClientError,
    resolve_ctx_size,
)


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    monkeypatch.setattr(GaiaConfig, "load", lambda: GaiaConfig(default_device="gpu"))


@pytest.mark.parametrize(
    "model,device,expected",
    [
        (None, "gpu", 65536),
        (None, "cpu", 65536),
        (None, "npu", 32768),
        ("user.local-model", "gpu", 65536),
        ("user.local-model", "npu", 32768),
        ("Qwen3-0.6B-GGUF", "gpu", 4096),
        ("gemma4-it-e2b-FLM", "gpu", 32768),
    ],
)
def test_defaults(model, device, expected):
    assert resolve_ctx_size(model, device) == expected


@pytest.mark.parametrize("value", ["16384", " 131072 "])
def test_gpu_override_replaces_registry_default(monkeypatch, value):
    before = deepcopy(MODELS)
    monkeypatch.setenv("GAIA_CTX_SIZE", value)
    assert resolve_ctx_size("Gemma-4-E4B-it-GGUF", "gpu") == int(value)
    assert MODELS == before


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_invalid_override_fails_loudly(monkeypatch, value):
    monkeypatch.setenv("GAIA_CTX_SIZE", value)
    with pytest.raises(LemonadeClientError, match="GAIA_CTX_SIZE.*positive integer"):
        resolve_ctx_size(device="gpu")


def test_npu_cap_warns_and_lower_override_is_honored(monkeypatch, caplog):
    monkeypatch.setenv("GAIA_CTX_SIZE", "131072")
    assert resolve_ctx_size(device="NPU") == 32768
    assert resolve_ctx_size("gemma4-it-e2b-FLM", "gpu") == 32768
    assert "NPU ceiling" in caplog.text
    monkeypatch.setenv("GAIA_CTX_SIZE", "16384")
    assert resolve_ctx_size(device="npu") == 16384


def test_unset_device_uses_persisted_npu(monkeypatch):
    monkeypatch.setattr(GaiaConfig, "load", lambda: GaiaConfig(default_device="npu"))
    assert resolve_ctx_size("user.local-model") == 32768


def test_blank_override_uses_default(monkeypatch):
    monkeypatch.setenv("GAIA_CTX_SIZE", "  ")
    assert resolve_ctx_size(device="gpu") == 65536


@pytest.mark.parametrize("window", [16384, 131072])
@responses.activate
def test_startup_switch_and_embedder_eviction_use_same_http_load(monkeypatch, window):
    """Exercise the actual client HTTP serialization for two successive reloads."""
    monkeypatch.setenv("GAIA_CTX_SIZE", str(window))
    base = "http://lemonade.test/api/v1"
    with (
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready", return_value=True
        ) as ready,
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url", return_value=base
        ),
    ):
        assert initialize_lemonade_for_agent("chat", base_url=base) == (True, base)
    assert ready.call_args.kwargs["min_context_size"] == window

    # An embedding model occupies the slot; the target LLM has been evicted.
    responses.get(
        base + "/health",
        json={
            "status": "ok",
            "all_models_loaded": [
                {
                    "model_name": "embed",
                    "type": "embedding",
                    "recipe_options": {"ctx_size": 8192},
                }
            ],
        },
    )
    responses.get(base + "/models", json={"data": []})
    responses.post(base + "/load", json={"status": "success"})
    client = LemonadeClient(base_url=base, keep_alive=True)
    for model in ["Gemma-4-E4B-it-GGUF", "user.another-local-model"]:
        client._ensure_model_loaded(model)  # pylint: disable=protected-access
    loads = [
        json.loads(call.request.body)
        for call in responses.calls
        if call.request.method == "POST"
    ]
    assert loads == [
        {"model_name": "Gemma-4-E4B-it-GGUF", "ctx_size": window},
        {"model_name": "user.another-local-model", "ctx_size": window},
    ]


def test_invalid_startup_override_returns_error_before_loading(monkeypatch, capsys):
    monkeypatch.setenv("GAIA_CTX_SIZE", "bad")
    with patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready") as ready:
        assert initialize_lemonade_for_agent("chat") == (False, None)
        ready.assert_not_called()
    assert "GAIA_CTX_SIZE" in capsys.readouterr().err


def test_instance_exact_pin_takes_precedence(monkeypatch):
    monkeypatch.setenv("GAIA_CTX_SIZE", "131072")
    client = LemonadeClient(ctx_size_override=4096, keep_alive=True)
    with patch.object(client, "_ensure_pinned_load") as pinned:
        client._ensure_model_loaded(
            "Gemma-4-E4B-it-GGUF"
        )  # pylint: disable=protected-access
        pinned.assert_called_once_with("Gemma-4-E4B-it-GGUF")
