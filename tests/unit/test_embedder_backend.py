# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The embedder loads on the GPU, and on the CPU only when the GPU load fails.

On a CPU backend, bulk indexing kept ~11 of 32 Strix Halo cores busy and made
the PC unusable (#5049). The Vulkan crash that once forced the CPU (#1831) no
longer reproduces with EmbeddingGemma, so the GPU is the default and the CPU is
a logged fallback, never a silent one.
"""

import json
import logging

import pytest

from gaia.llm import lemonade_client
from gaia.llm.lemonade_client import (
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDER_LLAMACPP_ARGS,
    EMBEDDER_ON_CPU_WITH_ROCM,
    LemonadeClient,
    LemonadeClientError,
    manages_embedder_backend,
)

_STARTUP_FAILURE = "model_load_error: llama-server failed to start"


@pytest.fixture
def not_macos(monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Windows")


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(lemonade_client, "_PINNED_BACKENDS", set())
    monkeypatch.setattr(lemonade_client, "_CPU_FALLBACK_EMBEDDERS", set())
    monkeypatch.setattr(lemonade_client.time, "sleep", lambda s: None)
    monkeypatch.setattr(lemonade_client, "preferred_llamacpp_backend", lambda: None)


def _recording_client(calls, default_backend="vulkan", failing_backends=()):
    """A client whose HTTP layer records calls and fails the named backends."""
    client = LemonadeClient(verbose=False)

    def fake_send(method, url, data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        calls.append((endpoint, dict(data or {})))
        if endpoint == "system-info":
            return {"recipes": {"llamacpp": {"default_backend": default_backend}}}
        if endpoint == "load" and (data or {}).get("llamacpp_backend") in (
            failing_backends
        ):
            raise LemonadeClientError(_STARTUP_FAILURE)
        if endpoint == "embeddings":
            return {"data": [{"embedding": [0.0, 1.0]}]}
        return {"status": "success"}

    client._send_request = fake_send
    return client


def _loads(calls):
    return [data for endpoint, data in calls if endpoint == "load"]


def test_only_the_embedder_backend_is_managed_under_either_name(not_macos):
    assert manages_embedder_backend(DEFAULT_EMBEDDING_MODEL)
    assert manages_embedder_backend("embeddinggemma-300m-GGUF")
    assert not manages_embedder_backend("Qwen3.6-35B-A3B-GGUF")
    assert not manages_embedder_backend("Gemma-4-E4B-it-GGUF")


def test_macos_leaves_the_embedder_to_lemonade(monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Darwin")
    calls = []
    client = _recording_client(calls)

    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert calls == [
        (
            "load",
            {
                "model_name": DEFAULT_EMBEDDING_MODEL,
                "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
            },
        )
    ]


def test_the_embedder_loads_on_lemonades_gpu_backend_and_saves_it(not_macos):
    """Naming the backend replaces a CPU pin an older GAIA saved in Lemonade."""
    calls = []
    client = _recording_client(calls)

    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert _loads(calls) == [
        {
            "model_name": DEFAULT_EMBEDDING_MODEL,
            "llamacpp_backend": "vulkan",
            "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
            "save_options": True,
        }
    ]


def test_a_gpu_startup_failure_falls_back_to_cpu_with_a_warning(not_macos, caplog):
    calls = []
    client = _recording_client(calls, failing_backends=("vulkan",))

    with caplog.at_level(logging.WARNING):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    backends = [load["llamacpp_backend"] for load in _loads(calls)]
    # One transient retry on the GPU, then the CPU.
    assert backends == ["vulkan", "vulkan", "cpu"]
    assert _loads(calls)[-1]["save_options"] is True
    assert client.model == DEFAULT_EMBEDDING_MODEL
    warning = next(
        r.getMessage()
        for r in caplog.records
        if "CPU backend instead" in r.getMessage()
    )
    assert DEFAULT_EMBEDDING_MODEL in warning
    assert "vulkan backend" in warning
    assert "llama-server failed to start" in warning


def test_after_a_fallback_the_process_loads_straight_onto_cpu(not_macos):
    calls = []
    client = _recording_client(calls, failing_backends=("vulkan",))
    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)
    calls.clear()

    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert [load["llamacpp_backend"] for load in _loads(calls)] == ["cpu"]


def test_a_cpu_failure_after_the_fallback_is_raised(not_macos):
    calls = []
    client = _recording_client(calls, failing_backends=("vulkan", "cpu"))

    with pytest.raises(LemonadeClientError, match="llama-server failed to start"):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False, load_retries=0)


def test_a_missing_model_does_not_fall_back(not_macos):
    client = LemonadeClient(verbose=False)
    sent = []

    def fake_send(method, url, data=None, **kwargs):
        if url.endswith("/system-info"):
            return {"recipes": {"llamacpp": {"default_backend": "vulkan"}}}
        sent.append(dict(data or {}))
        raise LemonadeClientError("model_not_found: no such model")

    client._send_request = fake_send

    with pytest.raises(LemonadeClientError, match="model_not_found"):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)
    assert [load["llamacpp_backend"] for load in sent] == ["vulkan"]


def test_a_cpu_only_pc_loads_on_cpu_without_a_fallback(not_macos, caplog):
    calls = []
    client = _recording_client(calls, default_backend="cpu")

    with caplog.at_level(logging.WARNING):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert [load["llamacpp_backend"] for load in _loads(calls)] == ["cpu"]
    assert "CPU backend instead" not in caplog.text


def test_an_unreadable_system_info_lets_lemonade_pick(not_macos, caplog):
    client = LemonadeClient(verbose=False)
    sent = []

    def fake_send(method, url, data=None, **kwargs):
        if url.endswith("/system-info"):
            raise LemonadeClientError("500 Internal Server Error")
        sent.append(dict(data or {}))
        return {"status": "success"}

    client._send_request = fake_send

    with caplog.at_level(logging.WARNING):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert "llamacpp_backend" not in sent[0]
    assert sent[0]["save_options"] is True
    assert "letting Lemonade choose it" in caplog.text


def test_the_first_embedding_loads_the_embedder_once(not_macos):
    """Lemonade's own auto-load would reuse a CPU pin saved by an older GAIA."""
    calls = []
    client = _recording_client(calls)
    client.model = "Qwen3.6-35B-A3B-GGUF"

    client.embeddings("one", model=DEFAULT_EMBEDDING_MODEL)
    client.embeddings("two", model=DEFAULT_EMBEDDING_MODEL)

    assert [endpoint for endpoint, _ in calls] == [
        "system-info",
        "load",
        "embeddings",
        "embeddings",
    ]
    assert _loads(calls)[0]["llamacpp_backend"] == "vulkan"
    assert client.model == "Qwen3.6-35B-A3B-GGUF"


def test_other_embedders_are_not_loaded_first(not_macos):
    calls = []
    client = _recording_client(calls)

    client.embeddings("one", model="nomic-embed-text-v2-moe-GGUF")

    assert [endpoint for endpoint, _ in calls] == ["embeddings"]


def test_an_explicit_embedder_args_string_is_sent_unchanged(not_macos):
    calls = []
    client = _recording_client(calls)

    client.load_model(
        DEFAULT_EMBEDDING_MODEL,
        prompt=False,
        llamacpp_args="--ubatch-size 2048 --split-mode none",
    )

    assert _loads(calls)[0]["llamacpp_args"] == "--ubatch-size 2048 --split-mode none"


def test_chat_models_get_no_embedder_ubatch_or_backend(not_macos):
    calls = []
    client = _recording_client(calls)

    client.load_model("Qwen3.6-35B-A3B-GGUF", prompt=False)

    assert _loads(calls) == [{"model_name": "Qwen3.6-35B-A3B-GGUF"}]


# ── ROCm: the embedder runs on the CPU (#5267) ────────────────────────────
# With the chat model and the embedder both on a ROCm GPU, an amdgpu driver
# issue holds the GPU at 100% while idle, so on ROCm the embedder takes the CPU.


@pytest.mark.parametrize("os_name", ["Linux", "Windows"])
def test_on_rocm_the_embedder_loads_on_the_cpu_and_says_so(
    monkeypatch, caplog, os_name
):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: os_name)
    calls = []
    client = _recording_client(calls, default_backend="rocm")

    with caplog.at_level(logging.INFO, logger=client.log.name):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert _loads(calls) == [
        {
            "model_name": DEFAULT_EMBEDDING_MODEL,
            "llamacpp_backend": "cpu",
            "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
            "save_options": True,
        }
    ]
    info = [r for r in caplog.records if EMBEDDER_ON_CPU_WITH_ROCM in r.getMessage()]
    assert info and info[0].levelno == logging.INFO
    assert "CPU backend instead" not in caplog.text


def test_choosing_rocm_for_chat_also_moves_the_embedder_to_the_cpu(
    not_macos, monkeypatch
):
    monkeypatch.setattr(lemonade_client, "preferred_llamacpp_backend", lambda: "rocm")
    calls = []
    client = _recording_client(calls, default_backend="vulkan")

    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert [load["llamacpp_backend"] for load in _loads(calls)] == ["cpu"]


@pytest.mark.parametrize(
    "default_backend,preferred", [("vulkan", None), ("vulkan", "vulkan"), ("cpu", None)]
)
def test_without_rocm_the_embedder_request_is_unchanged(
    not_macos, monkeypatch, caplog, default_backend, preferred
):
    monkeypatch.setattr(
        lemonade_client, "preferred_llamacpp_backend", lambda: preferred
    )
    calls = []
    client = _recording_client(calls, default_backend=default_backend)

    with caplog.at_level(logging.INFO, logger=client.log.name):
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert _loads(calls) == [
        {
            "model_name": DEFAULT_EMBEDDING_MODEL,
            "llamacpp_backend": default_backend,
            "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
            "save_options": True,
        }
    ]
    assert EMBEDDER_ON_CPU_WITH_ROCM not in caplog.text


def test_macos_is_untouched_even_if_lemonade_reports_rocm(monkeypatch):
    monkeypatch.setattr(lemonade_client.platform, "system", lambda: "Darwin")
    calls = []
    client = _recording_client(calls, default_backend="rocm")

    client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False)

    assert "llamacpp_backend" not in _loads(calls)[0]


def test_on_rocm_a_cpu_failure_is_raised_and_never_tries_the_gpu(not_macos):
    calls = []
    client = _recording_client(calls, default_backend="rocm", failing_backends=("cpu",))

    with pytest.raises(LemonadeClientError) as err:
        client.load_model(DEFAULT_EMBEDDING_MODEL, prompt=False, load_retries=0)

    assert [load["llamacpp_backend"] for load in _loads(calls)] == ["cpu"]
    message = str(err.value)
    assert "llama-server failed to start" in message
    assert "ROCm" in message
    assert "amd-gaia.ai/docs/reference/troubleshooting" in message
    # Not the Vulkan cooperative-matrix remedy, which says to use the CPU.
    assert "GGML_VK_DISABLE_COOPMAT" not in message


def test_on_rocm_the_first_embedding_loads_the_embedder_on_the_cpu(not_macos):
    calls = []
    client = _recording_client(calls, default_backend="rocm")
    # Keep a stopped GAIA Lemonade on this machine from redirecting the client.
    client._follow_gaia_lemonade = lambda: None

    client.embeddings("one", model=DEFAULT_EMBEDDING_MODEL)

    assert [endpoint for endpoint, _ in calls] == ["system-info", "load", "embeddings"]
    assert _loads(calls)[0]["llamacpp_backend"] == "cpu"


def _options_after_write_config(tmp_path, existing):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade

    manager = EmbeddedLemonade(home=tmp_path)
    options_path = manager.config_dir / "recipe_options.json"
    options_path.parent.mkdir(parents=True)
    options_path.write_text(json.dumps(existing))
    manager.write_config()
    return json.loads(options_path.read_text())


def test_embedded_lemonade_saves_the_embedder_ubatch_but_no_backend(
    tmp_path, not_macos
):
    options = _options_after_write_config(
        tmp_path, {"builtin.Qwen3.6-35B-A3B-GGUF": {"ctx_size": 65536}}
    )

    assert options[DEFAULT_EMBEDDING_MODEL] == {"llamacpp_args": EMBEDDER_LLAMACPP_ARGS}
    assert options["builtin.Qwen3.6-35B-A3B-GGUF"] == {"ctx_size": 65536}


def test_embedded_lemonade_clears_a_saved_cpu_pin_so_the_gpu_is_retried(
    tmp_path, not_macos
):
    options = _options_after_write_config(
        tmp_path,
        {
            DEFAULT_EMBEDDING_MODEL: {
                "llamacpp_backend": "cpu",
                "llamacpp_args": EMBEDDER_LLAMACPP_ARGS,
            }
        },
    )

    assert options[DEFAULT_EMBEDDING_MODEL] == {"llamacpp_args": EMBEDDER_LLAMACPP_ARGS}


def test_embedded_lemonade_keeps_a_gpu_backend_the_user_chose(tmp_path, not_macos):
    options = _options_after_write_config(
        tmp_path, {DEFAULT_EMBEDDING_MODEL: {"llamacpp_backend": "rocm"}}
    )

    assert options[DEFAULT_EMBEDDING_MODEL]["llamacpp_backend"] == "rocm"


def test_embedded_lemonade_keeps_an_existing_ubatch(tmp_path, not_macos):
    custom = "--ubatch-size 4096 --split-mode none"
    options = _options_after_write_config(
        tmp_path, {DEFAULT_EMBEDDING_MODEL: {"llamacpp_args": custom}}
    )

    assert options[DEFAULT_EMBEDDING_MODEL]["llamacpp_args"] == custom


def test_an_unreadable_options_file_fails_loudly(tmp_path, not_macos):
    from gaia.llm.lemonade_embedded import EmbeddedLemonade, EmbeddedLemonadeError

    manager = EmbeddedLemonade(home=tmp_path)
    options_path = manager.config_dir / "recipe_options.json"
    options_path.parent.mkdir(parents=True)
    options_path.write_text("[]")

    with pytest.raises(EmbeddedLemonadeError, match="not a JSON object"):
        manager.write_config()
