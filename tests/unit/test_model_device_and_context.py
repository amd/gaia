# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a local model runs (#5214), which models this PC can run (#5209), and
the context size a user may choose for one (#5213).

Lemonade is represented by recorded ``/health`` and ``/system-info`` bodies in
the shape a real server returns; the chosen-size test drives the real load path
and asserts the ``/load`` body Lemonade receives.
"""

# pylint: disable=protected-access

import json

import pytest
import responses

from gaia import config as config_mod
from gaia.llm import lemonade_client as lc
from gaia.llm.context_choice import (
    ContextChoiceError,
    check_saved_size,
    context_range,
    save_context_size,
    saved_context_size,
)
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    LARGE_DEFAULT_MODEL_NAME,
    LemonadeClient,
    LemonadeClientError,
    resolve_ctx_size,
)
from gaia.llm.model_device import (
    describe_placement,
    normalize_device,
    placement_label,
    recipe_unavailable_reason,
)
from gaia.llm.model_fit import MachineCapacity
from gaia.ui.local_models import annotate_local_models, friendly_name, measured_speeds

BASE = "http://lemonade.test/api/v1"


def _backends(**states):
    return {
        name: {"state": state, "message": msg} for name, (state, msg) in states.items()
    }


def system_info(*, npu=True, llamacpp_default="vulkan", gpu_backends=True):
    """``/system-info`` as Lemonade reports it, for one PC type."""
    llama = (
        _backends(
            vulkan=("installed", ""), rocm=("installable", ""), cpu=("installed", "")
        )
        if gpu_backends
        else _backends(
            vulkan=("unsupported", "No Vulkan driver"),
            cpu=("unsupported", "Unsupported CPU"),
        )
    )
    flm = (
        _backends(npu=("installable", "Backend is supported but not installed."))
        if npu
        else _backends(npu=("unsupported", "No NPU detected"))
    )
    return {
        "Physical Memory": "32.00 GB",
        "devices": {},
        "recipes": {
            "llamacpp": {
                "display_name": "Llama.cpp GPU",
                "default_backend": llamacpp_default,
                "backends": llama,
            },
            "flm": {"display_name": "FastFlowLM NPU", "backends": flm},
        },
    }


def loaded(name, device="gpu", recipe="llamacpp", backend_dir="vulkan", ctx=65536):
    """One ``/health`` ``all_models_loaded`` entry."""
    return {
        "model_name": name,
        "recipe": recipe,
        "device": device,
        "type": "llm",
        "status": "ready",
        "recipe_options": {"ctx_size": ctx},
        "launch_command": [
            f"C:\\lemonade\\bin\\llamacpp\\{backend_dir}\\llama-server.exe"
        ],
    }


def _cap(memory_gb, source="AMD iGPU", disk=None):
    return MachineCapacity(memory_gb=memory_gb, memory_source=source, disk_free_gb=disk)


# ── #5214: the device each model runs on ──────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("gpu", "gpu"),
        ("NPU", "npu"),
        ("igpu", "gpu"),
        ("npu, igpu", "npu+gpu"),
        ("", None),
    ],
)
def test_normalize_device(raw, expected):
    assert normalize_device(raw) == expected


def test_loaded_model_shows_the_device_lemonade_reports():
    health_entry = loaded("Gemma-4-E4B-it-GGUF", device="gpu", backend_dir="vulkan")
    p = describe_placement("llamacpp", health_entry, system_info())
    assert p == {
        "device": "gpu",
        "backend": "vulkan",
        "label": "GPU · Vulkan",
        "source": "reported",
        "warning": None,
    }


def test_loaded_model_that_fell_back_to_the_cpu_says_so():
    entry = loaded("Gemma-4-E4B-it-GGUF", device="cpu", backend_dir="cpu")
    p = describe_placement("llamacpp", entry, system_info())
    assert p["label"] == "CPU"
    assert p["source"] == "reported"
    assert "CPU instead of the GPU" in p["warning"]
    assert "slow" in p["warning"]


def test_unloaded_models_show_their_recipe_target_as_expected():
    gguf = describe_placement("llamacpp", None, system_info())
    assert (gguf["label"], gguf["source"]) == ("GPU · Vulkan", "expected")
    flm = describe_placement("flm", None, system_info())
    assert (flm["label"], flm["source"]) == ("NPU · FLM", "expected")
    rocm = describe_placement("llamacpp", None, system_info(), preferred_backend="rocm")
    assert rocm["label"] == "GPU · ROCm"


def test_npu_model_loaded_on_the_npu():
    entry = loaded("gemma4-it-e2b-FLM", device="npu", recipe="flm")
    p = describe_placement("flm", entry, system_info())
    assert (p["label"], p["source"], p["warning"]) == ("NPU · FLM", "reported", None)


def test_placement_label_without_a_backend():
    assert placement_label("cpu", "cpu") == "CPU"
    assert placement_label(None, "vulkan") is None


# ── #5209: only models this PC can run ────────────────────────────────────


def test_npu_models_are_unavailable_without_an_npu():
    reason = recipe_unavailable_reason("flm", system_info(npu=False))
    assert "Ryzen AI NPU" in reason and "No NPU detected" in reason
    assert recipe_unavailable_reason("flm", system_info(npu=True)) is None


def test_gguf_models_are_unavailable_without_a_llamacpp_backend():
    reason = recipe_unavailable_reason("llamacpp", system_info(gpu_backends=False))
    assert "Llama.cpp GPU" in reason and "No Vulkan driver" in reason


def test_an_unreported_recipe_is_not_hidden():
    assert recipe_unavailable_reason("llamacpp", {}) is None


_MODELS = [
    {
        "id": "Gemma-4-E4B-it-GGUF",
        "recipe": "llamacpp",
        "downloaded": True,
        "size_gb": 5.6,
    },
    {
        "id": LARGE_DEFAULT_MODEL_NAME,
        "recipe": "llamacpp",
        "downloaded": False,
        "size_gb": 21.7,
    },
    {"id": "gemma4-it-e2b-FLM", "recipe": "flm", "downloaded": True, "size_gb": 3.0},
]


@pytest.mark.parametrize(
    "pc,capacity,available",
    [
        # Strix Halo with an NPU: every model loads.
        (
            "halo",
            _cap(64),
            {"Gemma-4-E4B-it-GGUF", LARGE_DEFAULT_MODEL_NAME, "gemma4-it-e2b-FLM"},
        ),
        # 16 GB GPU, no NPU: the 35B does not fit and FLM has no NPU.
        ("small-gpu", _cap(16, "AMD GPU"), {"Gemma-4-E4B-it-GGUF"}),
    ],
)
def test_lists_offer_only_models_this_pc_runs(pc, capacity, available):
    out = annotate_local_models(
        _MODELS,
        health={"all_models_loaded": []},
        system_info=system_info(npu=pc == "halo"),
        capacity=capacity,
        preferred_backend=None,
        speeds={},
    )
    assert {m["id"] for m in out if m["available"]} == available
    for m in out:
        assert m["available"] == (m["unavailable_reason"] is None)


def test_too_large_model_says_why():
    out = annotate_local_models(
        _MODELS[1:2],
        health=None,
        system_info=system_info(),
        capacity=_cap(16, "AMD GPU", disk=500),
        preferred_backend=None,
        speeds={},
    )
    assert out[0]["fits"] is False
    assert "16 GB" in out[0]["unavailable_reason"]


def test_each_model_has_exactly_one_state():
    out = annotate_local_models(
        _MODELS,
        health={"all_models_loaded": [loaded("Gemma-4-E4B-it-GGUF")]},
        system_info=system_info(),
        capacity=_cap(64),
        preferred_backend=None,
        speeds={"Gemma-4-E4B-it-GGUF": 41.2},
    )
    by_id = {m["id"]: m for m in out}
    assert by_id["Gemma-4-E4B-it-GGUF"]["state"] == "loaded"
    assert by_id["Gemma-4-E4B-it-GGUF"]["placement"]["source"] == "reported"
    assert by_id["Gemma-4-E4B-it-GGUF"]["tokens_per_second"] == 41.2
    assert by_id[LARGE_DEFAULT_MODEL_NAME]["state"] == "not_downloaded"
    assert by_id["gemma4-it-e2b-FLM"]["state"] == "downloaded"


def test_an_idle_unload_clears_the_loaded_state():
    out = annotate_local_models(
        _MODELS[:1],
        health={"all_models_loaded": []},
        system_info=system_info(),
        capacity=_cap(64),
        preferred_backend=None,
        speeds={},
    )
    assert out[0]["state"] == "downloaded"


def test_a_running_model_is_never_hidden_by_the_estimate():
    out = annotate_local_models(
        _MODELS[1:2],
        health={"all_models_loaded": [loaded(LARGE_DEFAULT_MODEL_NAME)]},
        system_info=system_info(),
        capacity=_cap(16, "AMD GPU"),
        preferred_backend=None,
        speeds={},
    )
    assert out[0]["available"] is True


# ── Friendly names and measured speed ─────────────────────────────────────


@pytest.mark.parametrize(
    "model_id,name",
    [
        (DEFAULT_MODEL_NAME, "Gemma 4 E4B"),
        (LARGE_DEFAULT_MODEL_NAME, "Qwen3.6 35B A3B"),
        ("gemma4-it-e2b-FLM", "Gemma 4 E2B"),
        ("Qwen3-30B-A3B-Instruct-2507-GGUF", "Qwen3 30B A3B 2507"),
        ("user.Phi-4-mini-instruct-GGUF", "Phi 4 mini"),
        # A user-registered build: date stamp and format tag dropped, sizes upper-case.
        ("user.qwen38-27b-gguf-20260826", "Qwen38 27B"),
    ],
)
def test_friendly_name(model_id, name):
    assert friendly_name(model_id) == name


def test_measured_speed_is_the_median_of_recent_turns():
    stats = [
        {"measured_model": "A", "tokens_per_second": 10.0},
        {"measured_model": "A", "tokens_per_second": 30.0},
        {"measured_model": "A", "tokens_per_second": 20.0},
        {"measured_model": "B", "tokens_per_second": 0},
        {"tokens_per_second": 99.0},
        # A kept Retry with… answer names its model under "model"; not a speed sample.
        {"model": "A", "tokens_per_second": 1.0},
    ]
    assert measured_speeds(stats) == {"A": 20.0}


# ── #5213: the context size a user may choose ─────────────────────────────


def test_context_options_stop_at_what_fits():
    big = context_range(LARGE_DEFAULT_MODEL_NAME, _cap(128))
    assert big["options"] == [65536, 131072, 262144]
    assert big["options"][-1] == big["max_fit"] == big["automatic"]
    mid = context_range(LARGE_DEFAULT_MODEL_NAME, _cap(32, "AMD GPU"))
    assert mid["options"][-1] == mid["max_fit"] < 262144
    assert all(o <= mid["max_fit"] for o in mid["options"])
    assert mid["options"][0] == mid["min"] == 65536


def test_a_model_without_a_window_range_offers_only_its_floor():
    span = context_range(DEFAULT_MODEL_NAME, _cap(128))
    assert span["options"] == [65536]


def test_an_unknown_model_cannot_be_sized():
    assert context_range("Mystery-GGUF", _cap(64)) is None
    with pytest.raises(ContextChoiceError, match="does not know"):
        save_context_size("Mystery-GGUF", 65536, _cap(64))


def test_chosen_size_persists_and_automatic_clears_it():
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"last_model": "x"}), encoding="utf-8"
    )
    save_context_size(LARGE_DEFAULT_MODEL_NAME, 131072, _cap(128))
    assert saved_context_size(LARGE_DEFAULT_MODEL_NAME) == 131072
    on_disk = json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["last_model"] == "x"
    save_context_size(LARGE_DEFAULT_MODEL_NAME, None, _cap(128))
    assert saved_context_size(LARGE_DEFAULT_MODEL_NAME) is None
    assert "context_sizes" not in json.loads(
        config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8")
    )


def test_saving_a_size_keeps_other_models_and_unknown_keys():
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"tui_only_key": [1, 2], "context_sizes": {"Other-GGUF": 32768}}),
        encoding="utf-8",
    )
    save_context_size(LARGE_DEFAULT_MODEL_NAME, 131072, _cap(128))
    on_disk = json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["tui_only_key"] == [1, 2]
    assert on_disk["context_sizes"] == {
        "Other-GGUF": 32768,
        LARGE_DEFAULT_MODEL_NAME: 131072,
    }
    # Only the touched key is written; defaults for undeclared fields are not.
    assert set(on_disk) == {"tui_only_key", "context_sizes"}


def test_saving_a_size_rereads_the_file_under_the_lock():
    """A key another process wrote after this one last loaded still survives."""
    config_mod.GAIA_CONFIG_FILE.write_text("{}", encoding="utf-8")
    config_mod.GaiaConfig.load()
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"default_model": "Elsewhere-GGUF"}), encoding="utf-8"
    )
    save_context_size(LARGE_DEFAULT_MODEL_NAME, 131072, _cap(128))
    on_disk = json.loads(config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8"))
    assert on_disk["default_model"] == "Elsewhere-GGUF"


def test_saving_a_size_refuses_to_overwrite_a_corrupt_config():
    config_mod.GAIA_CONFIG_FILE.write_text("{not json", encoding="utf-8")
    with pytest.raises(config_mod.GaiaConfigError, match="not valid JSON"):
        save_context_size(LARGE_DEFAULT_MODEL_NAME, 131072, _cap(128))
    assert config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8") == "{not json"


def test_saving_a_size_refuses_when_the_existing_sizes_are_malformed():
    bad = json.dumps({"context_sizes": {"Other-GGUF": "big"}})
    config_mod.GAIA_CONFIG_FILE.write_text(bad, encoding="utf-8")
    with pytest.raises(config_mod.GaiaConfigError, match="context_sizes"):
        save_context_size(LARGE_DEFAULT_MODEL_NAME, 131072, _cap(128))
    assert config_mod.GAIA_CONFIG_FILE.read_text(encoding="utf-8") == bad


def test_a_size_not_offered_is_refused():
    with pytest.raises(ContextChoiceError, match="not offered"):
        save_context_size(LARGE_DEFAULT_MODEL_NAME, 262144, _cap(32, "AMD GPU"))


def test_a_saved_size_that_no_longer_fits_is_refused():
    with pytest.raises(ContextChoiceError, match="does not fit this PC"):
        check_saved_size(LARGE_DEFAULT_MODEL_NAME, 262144, _cap(32, "AMD GPU"))


def test_config_rejects_a_malformed_context_sizes():
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"context_sizes": {"m": "big"}}), encoding="utf-8"
    )
    # Looked up at call time: test_cli_config reloads gaia.config, replacing the class.
    with pytest.raises(config_mod.GaiaConfigError, match="context_sizes"):
        config_mod.GaiaConfig.load()
    with pytest.raises(config_mod.GaiaConfigError, match="Settings › Model"):
        config_mod.GaiaConfig().set("context_sizes", "{}")


@pytest.fixture
def halo_lemonade(monkeypatch):
    """An idle 128 GB Strix Halo Lemonade, read fresh by this test."""
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    lc._CAPACITY_CACHE.clear()
    lc._SYSTEM_INFO_CACHE.clear()
    info = {
        "devices": {
            "amd_igpu": {
                "name": "AMD Radeon 8060S",
                "available": True,
                "vram_gb": 96,
                "virtual_mem_gb": 16,
            }
        }
    }
    responses.get(BASE + "/health", json={"status": "ok", "all_models_loaded": []})
    responses.get(BASE + "/models", json={"data": []})
    responses.get(BASE + "/system-info", json=info)
    responses.post(BASE + "/load", json={"status": "success"})
    yield
    lc._CAPACITY_CACHE.clear()
    lc._SYSTEM_INFO_CACHE.clear()


def _load_bodies():
    return [
        json.loads(c.request.body)
        for c in responses.calls
        if c.request.method == "POST" and c.request.url.endswith("/load")
    ]


@responses.activate
def test_chosen_size_reaches_lemonades_load_request(halo_lemonade):
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"context_sizes": {LARGE_DEFAULT_MODEL_NAME: 131072}}),
        encoding="utf-8",
    )
    assert resolve_ctx_size(LARGE_DEFAULT_MODEL_NAME, base_url=BASE) == 131072
    LemonadeClient(base_url=BASE, keep_alive=True)._ensure_model_loaded(
        LARGE_DEFAULT_MODEL_NAME
    )
    assert _load_bodies() == [
        {"model_name": LARGE_DEFAULT_MODEL_NAME, "ctx_size": 131072}
    ]


@responses.activate
def test_load_refuses_a_saved_size_this_pc_cannot_hold(halo_lemonade):
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"context_sizes": {LARGE_DEFAULT_MODEL_NAME: 1048576}}),
        encoding="utf-8",
    )
    with pytest.raises(LemonadeClientError, match="does not fit this PC"):
        resolve_ctx_size(LARGE_DEFAULT_MODEL_NAME, base_url=BASE)
    assert _load_bodies() == []


@responses.activate
def test_a_saved_size_is_not_loaded_unchecked_when_memory_is_unreadable(monkeypatch):
    """The promise is that a size that may not fit is refused, not loaded blind."""
    monkeypatch.delenv("GAIA_CTX_SIZE", raising=False)
    lc._CAPACITY_CACHE.clear()
    lc._SYSTEM_INFO_CACHE.clear()
    # A /system-info naming no memory at all: GAIA cannot judge any window.
    responses.get(BASE + "/system-info", json={"devices": {}})
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"context_sizes": {LARGE_DEFAULT_MODEL_NAME: 131072}}),
        encoding="utf-8",
    )
    try:
        with pytest.raises(
            LemonadeClientError, match="can't read this PC's memory"
        ) as e:
            resolve_ctx_size(LARGE_DEFAULT_MODEL_NAME, base_url=BASE)
        assert "Choose Automatic in Settings › Model" in str(e.value)
        assert _load_bodies() == []
    finally:
        lc._CAPACITY_CACHE.clear()
        lc._SYSTEM_INFO_CACHE.clear()


@responses.activate
def test_gaia_ctx_size_rescues_a_saved_size_that_no_longer_fits(
    halo_lemonade, monkeypatch
):
    config_mod.GAIA_CONFIG_FILE.write_text(
        json.dumps({"context_sizes": {LARGE_DEFAULT_MODEL_NAME: 1048576}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIA_CTX_SIZE", "65536")
    assert resolve_ctx_size(LARGE_DEFAULT_MODEL_NAME, base_url=BASE) == 65536
